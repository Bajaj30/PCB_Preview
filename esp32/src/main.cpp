/*
 * main.cpp — PCB Plotter Entry Point (Phase 8)
 *
 * ESP32-WROOM-32 + 2x TMC2209 (UART, StealthChop)
 *
 * Architecture:
 *   Core 0 (Arduino loop): WiFi + HTTP server (always responsive)
 *   Core 1 (motionTask):   Pulls G-code from queue, drives motors
 *
 * The FreeRTOS queue decouples network I/O from motor control,
 * ensuring Emergency Stop is always instant.
 *
 * Modules:
 *   motion       — GPIO setup, stepper pulses, Bresenham XY movement
 *   gcode        — G-code parser and executor
 *   wifi_server  — WiFi connection, HTTP server, /gcode endpoint
 *   laser        — PWM laser control
 */

#include <Arduino.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include "motion.h"
#include "gcode.h"
#include "wifi_server.h"
#include "laser.h"

// ── FreeRTOS G-code queue ─────────────────────────────────────────
// Each slot holds one G-code line (max 255 chars + null terminator).
// Queue depth of 5 gives the web UI room to push lines ahead of
// the motor, but is small enough that STOP takes effect quickly.
#define GCODE_LINE_MAX   256
#define GCODE_QUEUE_DEPTH  5

QueueHandle_t gcodeQueue = NULL;

// ── Motion task (runs on Core 1) ──────────────────────────────────
// Waits for lines in the queue, executes them one by one.
// Between each line it checks the stop flag so abort is near-instant.
void motionTask(void* pvParameters) {
    char line[GCODE_LINE_MAX];

    Serial.println("[motion-task] Started on Core 1");

    for (;;) {
        // Block until a line arrives (portMAX_DELAY = wait forever)
        if (xQueueReceive(gcodeQueue, line, portMAX_DELAY) == pdTRUE) {
            // Check stop flag before executing
            if (getStopFlag()) {
                Serial.println("[motion-task] STOP flag set — skipping line");
                continue;
            }

            Serial.printf("[motion-task] >> %s\n", line);
            executeGCodeLine(line);
        }
    }
}

// ── Serial input buffer ───────────────────────────────────────────
static char serialBuffer[256];
static int serialIdx = 0;

void setup() {
    // Disable brownout detector to prevent reset on current spikes
    WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

    Serial.begin(115200);
    delay(2000);

    Serial.println("=========================================");
    Serial.println("  PCB Plotter — Phase 8 (Command Center)");
    Serial.println("  ESP32 + TMC2209 UART + FreeRTOS");
    Serial.println("=========================================");

    initMotion();      // configure STEP/DIR GPIO + TMC2209 UART
    initLaser();       // configure Laser PWM on GPIO 25
    initGCode();       // initialize G-code interpreter

    // Create the G-code queue BEFORE starting WiFi (which registers /gcode)
    gcodeQueue = xQueueCreate(GCODE_QUEUE_DEPTH, GCODE_LINE_MAX);
    if (gcodeQueue == NULL) {
        Serial.println("[FATAL] Failed to create G-code queue!");
        while (1) delay(1000);  // halt
    }
    Serial.printf("[main] G-code queue created (depth=%d, line_max=%d)\n",
                  GCODE_QUEUE_DEPTH, GCODE_LINE_MAX);

    initWiFi();        // scan networks + connect to AP
    initWebServer();   // register routes + start HTTP server

    // Spawn the motion task on Core 1
    // Stack: 8192 bytes (plenty for Bresenham + G-code parsing)
    // Priority: 1 (above idle but below WiFi tasks)
    xTaskCreatePinnedToCore(
        motionTask,     // function
        "motionTask",   // name
        8192,           // stack size (bytes)
        NULL,           // parameters
        1,              // priority
        NULL,           // task handle (not needed)
        1               // Core 1
    );

    Serial.println();
    Serial.println("[main] Ready! Open http://<IP>/ for D-pad control");
    Serial.println("[main] G-code streaming via POST /gcode from Web UI");
    Serial.println("[main] Serial G-code input also available below");
    Serial.println("=========================================");
}

void loop() {
    // Core 0: Handle web server requests (always responsive)
    handleServer();

    // Core 0: Handle serial G-code input (debug/testing)
    while (Serial.available()) {
        char c = Serial.read();

        if (c == '\n' || c == '\r') {
            if (serialIdx > 0) {
                serialBuffer[serialIdx] = '\0';

                // Check for special commands
                if (strcmp(serialBuffer, "status") == 0 || strcmp(serialBuffer, "?") == 0) {
                    Serial.printf("[status] Position: (%.2f, %.2f) mm\n",
                                  getPositionX(), getPositionY());
                    Serial.printf("[status] G-code finished: %s\n",
                                  isGCodeFinished() ? "yes" : "no");
                    Serial.printf("[status] Queue slots free: %d/%d\n",
                                  uxQueueSpacesAvailable(gcodeQueue), GCODE_QUEUE_DEPTH);
                } else if (strcmp(serialBuffer, "reset") == 0) {
                    resetGCode();
                    Serial.println("[main] G-code interpreter reset");
                } else if (strcmp(serialBuffer, "stop") == 0) {
                    setStopFlag(true);
                    turnLaserOff();
                    // Flush the queue so queued lines don't execute after stop
                    xQueueReset(gcodeQueue);
                    Serial.println("[main] STOP — queue flushed, laser off");
                } else {
                    // Push to queue (serial commands also go through the queue)
                    setStopFlag(false);
                    if (xQueueSend(gcodeQueue, serialBuffer, 0) != pdTRUE) {
                        Serial.println("[main] Queue full — try again");
                    }
                }

                serialIdx = 0;
            }
        } else {
            if (serialIdx < (int)sizeof(serialBuffer) - 1) {
                serialBuffer[serialIdx++] = c;
            }
        }
    }
}

