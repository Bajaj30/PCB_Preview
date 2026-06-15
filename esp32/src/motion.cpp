/*
 * motion.cpp — Stepper motor pulse generation & coordinate movement
 *
 * Extracted from monolithic main.cpp, extended for Phase 5.
 * Drives TMC2209 drivers in standalone mode via STEP/DIR signals.
 *
 * Features:
 *   - Single-axis step pulse generation
 *   - Bresenham synchronized XY movement
 *   - Position tracking in mm
 *   - Feed rate control (mm/min)
 *   - Emergency stop flag
 */

#include "motion.h"
#include "laser.h"
#include <TMCStepper.h>

// ── Hardware Serial for TMC2209 ───────────────────────────────────
HardwareSerial SerialTMC_X(1);
HardwareSerial SerialTMC_Y(2);

TMC2209Stepper driverX(&SerialTMC_X, R_SENSE, DRIVER_ADDRESS);
TMC2209Stepper driverY(&SerialTMC_Y, R_SENSE, DRIVER_ADDRESS);

// ── State ─────────────────────────────────────────────────────────
static volatile bool stopFlag = false;
static float currentX = 0.0;  // current position in mm
static float currentY = 0.0;

// ── Public API ────────────────────────────────────────────────────

void initMotion() {
    pinMode(X_STEP_PIN, OUTPUT);
    pinMode(X_DIR_PIN,  OUTPUT);
    pinMode(Y_STEP_PIN, OUTPUT);
    pinMode(Y_DIR_PIN,  OUTPUT);

    currentX = 0.0;
    currentY = 0.0;

    Serial.println("[motion] GPIO initialized");
    
    // ── Initialize TMC2209 UART ───────────────────────────────────
    Serial.println("[motion] Initializing TMC2209 UART drivers...");
    
    // ESP32 HardwareSerial allows half-duplex by using the same pin for RX and TX
    SerialTMC_X.begin(115200, SERIAL_8N1, X_UART_PIN, X_UART_PIN);
    SerialTMC_Y.begin(115200, SERIAL_8N1, Y_UART_PIN, Y_UART_PIN);

    // Configure X Axis
    driverX.begin();
    driverX.toff(5);                     // Enables driver
    driverX.rms_current(MOTOR_CURRENT_MA);
    driverX.microsteps(16);
    driverX.en_spreadCycle(false);       // Enable StealthChop (quiet mode)
    driverX.pwm_autoscale(true);

    // Configure Y Axis
    driverY.begin();
    driverY.toff(5);
    driverY.rms_current(MOTOR_CURRENT_MA);
    driverY.microsteps(16);
    driverY.en_spreadCycle(false);
    driverY.pwm_autoscale(true);

    // Test connections
    uint8_t testX = driverX.test_connection();
    uint8_t testY = driverY.test_connection();
    
    if (testX == 0) Serial.printf("[motion] X Driver (GPIO %d): OK\n", X_UART_PIN);
    else Serial.printf("[motion] X Driver (GPIO %d): ERROR (code %d)\n", X_UART_PIN, testX);
    
    if (testY == 0) Serial.printf("[motion] Y Driver (GPIO %d): OK\n", Y_UART_PIN);
    else Serial.printf("[motion] Y Driver (GPIO %d): ERROR (code %d)\n", Y_UART_PIN, testY);

    Serial.printf("[motion] Current: %dmA, Microsteps: 1/16, StealthChop: ON\n", MOTOR_CURRENT_MA);
}

void stepMotor(uint8_t stepPin, uint8_t dirPin, int dir, int steps) {
    digitalWrite(dirPin, dir ? HIGH : LOW);
    delayMicroseconds(5); // settle direction signal

    for (int i = 0; i < steps && !stopFlag; i++) {
        digitalWrite(stepPin, HIGH);
        delayMicroseconds(PULSE_WIDTH_US);
        digitalWrite(stepPin, LOW);
        delayMicroseconds(STEP_DELAY_US);
    }
}

void moveToXY(float x_mm, float y_mm, float feed_rate) {
    // Calculate delta in steps
    long targetX = (long)(x_mm * STEPS_PER_MM);
    long targetY = (long)(y_mm * STEPS_PER_MM);
    long curX = (long)(currentX * STEPS_PER_MM);
    long curY = (long)(currentY * STEPS_PER_MM);

    long dx = targetX - curX;
    long dy = targetY - curY;

    // Nothing to do
    if (dx == 0 && dy == 0) return;

    // Direction
    int xDir = (dx >= 0) ? 1 : 0;
    int yDir = (dy >= 0) ? 1 : 0;

    long absDx = abs(dx);
    long absDy = abs(dy);

    // Set direction pins
    digitalWrite(X_DIR_PIN, xDir ? HIGH : LOW);
    digitalWrite(Y_DIR_PIN, yDir ? HIGH : LOW);
    delayMicroseconds(5); // settle direction

    // Calculate step delay from feed rate
    // feed_rate is in mm/min, convert to us per step
    unsigned long stepDelay;
    if (feed_rate <= 0) {
        // Rapid move — use minimum delay
        stepDelay = STEP_DELAY_US;
    } else {
        // Calculate actual distance for speed computation
        float dist_mm = sqrt((float)(dx * dx + dy * dy)) / STEPS_PER_MM;
        float total_steps = max(absDx, absDy);

        if (total_steps == 0) return;

        // Time for entire move in seconds
        float time_sec = (dist_mm / feed_rate) * 60.0;
        // Time per step in microseconds
        stepDelay = (unsigned long)((time_sec * 1000000.0) / total_steps);

        // Clamp to reasonable range
        if (stepDelay < PULSE_WIDTH_US + 5) stepDelay = PULSE_WIDTH_US + 5;
        if (stepDelay > 50000) stepDelay = 50000;  // max 50ms per step = very slow
    }

    // Bresenham line algorithm for synchronized XY movement
    long steps = max(absDx, absDy);
    long errX = 0;
    long errY = 0;

    Serial.printf("[motion] moveToXY(%.2f, %.2f) steps=%ld delay=%luus\n",
                  x_mm, y_mm, steps, stepDelay);

    for (long i = 0; i < steps && !stopFlag; i++) {
        errX += absDx;
        errY += absDy;

        bool stepX = false;
        bool stepY = false;

        if (errX >= steps) {
            errX -= steps;
            stepX = true;
        }
        if (errY >= steps) {
            errY -= steps;
            stepY = true;
        }

        // Pulse step pins simultaneously for synchronized movement
        if (stepX) digitalWrite(X_STEP_PIN, HIGH);
        if (stepY) digitalWrite(Y_STEP_PIN, HIGH);

        delayMicroseconds(PULSE_WIDTH_US);

        if (stepX) digitalWrite(X_STEP_PIN, LOW);
        if (stepY) digitalWrite(Y_STEP_PIN, LOW);

        delayMicroseconds(stepDelay);
    }

    // Update position (even if stopped early, track actual position)
    if (!stopFlag) {
        currentX = x_mm;
        currentY = y_mm;
    }
}

void moveRelative(float dx_mm, float dy_mm, float feed_rate) {
    moveToXY(currentX + dx_mm, currentY + dy_mm, feed_rate);
}

void home() {
    Serial.println("[motion] Homing to (0, 0)");
    moveToXY(0.0, 0.0, 0);  // rapid move to origin
}

float getPositionX() {
    return currentX;
}

float getPositionY() {
    return currentY;
}

void setStopFlag(bool flag) {
    stopFlag = flag;
    if (flag) {
        turnLaserOff(); // Safety: Immediately kill laser power on emergency stop
    }
}

bool getStopFlag() {
    return stopFlag;
}
