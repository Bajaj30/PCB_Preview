/*
 * gcode.cpp — G-Code interpreter implementation
 *
 * Parses G-code lines, extracts parameters (X, Y, F, P),
 * and dispatches motion commands to the motion module.
 *
 * Supports a subset of standard G-code sufficient for PCB plotting:
 *   G0  — rapid positioning
 *   G1  — linear interpolation (drawing)
 *   G4  — dwell
 *   G21 — millimeter units
 *   G28 — home
 *   G90 — absolute positioning
 *   G91 — relative positioning
 *   M2  — program end
 */

#include "gcode.h"
#include "motion.h"
#include "laser.h"
#include <string.h>
#include <stdlib.h>
#include <ctype.h>

// ── Interpreter state ─────────────────────────────────────────────
static bool absoluteMode = true;      // G90 = true, G91 = false
static float currentFeedRate = DEFAULT_FEED_RATE;
static float currentLaserPower = 0.0;
static bool programFinished = false;
static int linesExecuted = 0;

// ── Parameter extraction helpers ──────────────────────────────────

/**
 * Find a parameter letter in the G-code line and return its float value.
 * E.g., findParam("G1 X10.5 Y20", 'X') returns 10.5 and sets found=true.
 */
static float findParam(const char* line, char param, bool* found) {
    *found = false;
    const char* p = line;

    while (*p) {
        if (toupper(*p) == toupper(param)) {
            p++;
            // Skip any spaces between letter and number
            while (*p == ' ') p++;
            if (*p == '-' || *p == '+' || *p == '.' || isdigit(*p)) {
                *found = true;
                return atof(p);
            }
        }
        p++;
    }
    return 0.0;
}

/**
 * Extract the G or M command number from a line.
 * Returns -1 if not found.
 */
static int findCommand(const char* line, char cmdLetter) {
    const char* p = line;

    while (*p) {
        if (toupper(*p) == cmdLetter) {
            p++;
            while (*p == ' ') p++;
            if (isdigit(*p)) {
                return atoi(p);
            }
        }
        p++;
    }
    return -1;
}

// ── Public API ────────────────────────────────────────────────────

void initGCode() {
    absoluteMode = true;
    currentFeedRate = DEFAULT_FEED_RATE;
    programFinished = false;
    linesExecuted = 0;

    Serial.println("[gcode] Interpreter initialized");
    Serial.printf("[gcode] Mode: absolute, Feed: %.0f mm/min\n", currentFeedRate);
}

bool executeGCodeLine(const char* line) {
    // Skip empty lines
    if (!line || strlen(line) == 0) return true;

    // Skip leading whitespace
    while (*line == ' ' || *line == '\t') line++;

    // Skip comment-only lines
    if (*line == ';' || *line == '\0' || *line == '\n' || *line == '\r') return true;

    // Make a working copy and strip inline comments
    char buf[256];
    strncpy(buf, line, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    // Strip inline comment
    char* comment = strchr(buf, ';');
    if (comment) *comment = '\0';

    // Strip trailing whitespace/newline
    int len = strlen(buf);
    while (len > 0 && (buf[len-1] == ' ' || buf[len-1] == '\n' || buf[len-1] == '\r')) {
        buf[--len] = '\0';
    }

    // Empty after stripping
    if (len == 0) return true;

    // Check for program end
    if (programFinished) {
        Serial.println("[gcode] Program already finished (M2). Call resetGCode().");
        return false;
    }

    // Parse G commands
    int gCmd = findCommand(buf, 'G');
    int mCmd = findCommand(buf, 'M');

    bool hasX, hasY, hasF, hasP, hasS;
    float xVal = findParam(buf, 'X', &hasX);
    float yVal = findParam(buf, 'Y', &hasY);
    float fVal = findParam(buf, 'F', &hasF);
    float pVal = findParam(buf, 'P', &hasP);
    float sVal = findParam(buf, 'S', &hasS);

    // Update feed rate if specified
    if (hasF && fVal > 0) {
        currentFeedRate = fVal;
        if (currentFeedRate > MAX_FEED_RATE) {
            currentFeedRate = MAX_FEED_RATE;
        }
    }

    // Update laser power if specified
    if (hasS) {
        currentLaserPower = sVal;
        // If the laser is currently active, apply the new power immediately
        if (isLaserOn()) {
            setLaserPower(currentLaserPower);
        }
    }

    // Dispatch G commands
    if (gCmd >= 0) {
        switch (gCmd) {
            case 0: {
                // G0 — Rapid positioning
                float targetX = hasX ? xVal : (absoluteMode ? getPositionX() : 0);
                float targetY = hasY ? yVal : (absoluteMode ? getPositionY() : 0);

                if (absoluteMode) {
                    moveToXY(targetX, targetY, 0);  // 0 = rapid
                } else {
                    moveRelative(targetX, targetY, 0);
                }
                linesExecuted++;
                return true;
            }

            case 1: {
                // G1 — Linear interpolation (drawing move)
                float targetX = hasX ? xVal : (absoluteMode ? getPositionX() : 0);
                float targetY = hasY ? yVal : (absoluteMode ? getPositionY() : 0);

                if (absoluteMode) {
                    moveToXY(targetX, targetY, currentFeedRate);
                } else {
                    moveRelative(targetX, targetY, currentFeedRate);
                }
                linesExecuted++;
                return true;
            }

            case 4: {
                // G4 — Dwell
                if (hasP) {
                    Serial.printf("[gcode] Dwell %d ms\n", (int)pVal);
                    delay((int)pVal);
                }
                linesExecuted++;
                return true;
            }

            case 21:
                // G21 — Millimeters (our native unit, nothing to do)
                Serial.println("[gcode] Units: millimeters");
                linesExecuted++;
                return true;

            case 28:
                // G28 — Home
                home();
                linesExecuted++;
                return true;

            case 90:
                // G90 — Absolute positioning
                absoluteMode = true;
                Serial.println("[gcode] Mode: absolute");
                linesExecuted++;
                return true;

            case 91:
                // G91 — Relative positioning
                absoluteMode = false;
                Serial.println("[gcode] Mode: relative");
                linesExecuted++;
                return true;

            default:
                Serial.printf("[gcode] Unsupported: G%d (skipping)\n", gCmd);
                linesExecuted++;
                return true;  // skip unknown G-codes gracefully
        }
    }

    // Dispatch M commands
    if (mCmd >= 0) {
        switch (mCmd) {
            case 2:
                // M2 — Program end
                turnLaserOff();
                Serial.println("[gcode] === Program End (M2) ===");
                Serial.printf("[gcode] Lines executed: %d\n", linesExecuted);
                Serial.printf("[gcode] Final position: (%.2f, %.2f) mm\n",
                              getPositionX(), getPositionY());
                programFinished = true;
                return true;

            case 3:
            case 4:
                // M3 / M4 — Laser On
                setLaserPower(currentLaserPower);
                Serial.printf("[gcode] Laser ON (S%.1f)\n", currentLaserPower);
                return true;

            case 5:
                // M5 — Laser Off
                turnLaserOff();
                Serial.println("[gcode] Laser OFF");
                return true;

            default:
                Serial.printf("[gcode] Unsupported: M%d (skipping)\n", mCmd);
                return true;
        }
    }

    // Unknown command — log but don't fail
    Serial.printf("[gcode] Unknown line: %s\n", buf);
    return true;
}

int executeGCodeProgram(const char* program) {
    if (!program) return 0;

    int count = 0;
    char lineBuf[256];
    int lineIdx = 0;

    const char* p = program;
    while (*p && !programFinished && !getStopFlag()) {
        if (*p == '\n' || *p == '\r') {
            lineBuf[lineIdx] = '\0';
            if (lineIdx > 0) {
                executeGCodeLine(lineBuf);
                count++;
            }
            lineIdx = 0;
            // Skip \r\n
            if (*p == '\r' && *(p+1) == '\n') p++;
        } else {
            if (lineIdx < (int)sizeof(lineBuf) - 1) {
                lineBuf[lineIdx++] = *p;
            }
        }
        p++;
    }

    // Handle last line without trailing newline
    if (lineIdx > 0 && !programFinished && !getStopFlag()) {
        lineBuf[lineIdx] = '\0';
        executeGCodeLine(lineBuf);
        count++;
    }

    return count;
}

bool isGCodeFinished() {
    return programFinished;
}

void resetGCode() {
    programFinished = false;
    linesExecuted = 0;
    absoluteMode = true;
    currentFeedRate = DEFAULT_FEED_RATE;
    currentLaserPower = 0.0;
    turnLaserOff();
    Serial.println("[gcode] Interpreter reset");
}
