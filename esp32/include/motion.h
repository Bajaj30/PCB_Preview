/*
 * motion.h — Stepper motor control for PCB Plotter
 *
 * Hardware:
 *   ESP32-WROOM-32 + 2x TMC2209 (standalone, 1/16 microstepping)
 *
 * TMC2209 standalone wiring (hardwire on PCB):
 *   EN      -> GND           (always enabled, active LOW)
 *   MS1     -> 3.3V          |  1/16 microstepping
 *   MS2     -> 3.3V          |  (MS1=H, MS2=H)
 *   PDN/UART-> GND via 100Ω  (disable UART, standalone mode)
 *
 * Microstepping: 200 full steps × 16 = 3200 microsteps/rev
 * GT2 belt, 20-tooth pulley, 2mm pitch → 40mm/rev
 * Resolution: 3200 / 40 = 80 steps/mm
 */

#ifndef MOTION_H
#define MOTION_H

#include <Arduino.h>

// ── Pin definitions ───────────────────────────────────────────────
#define X_STEP_PIN  21
#define X_DIR_PIN   19
#define X_UART_PIN  16

#define Y_STEP_PIN  33
#define Y_DIR_PIN   32
#define Y_UART_PIN  17

// ── TMC2209 Configuration ─────────────────────────────────────────
#define R_SENSE           0.11f   // Standard TMC2209 sense resistor
#define DRIVER_ADDRESS    0       // Default address (MS1=GND, MS2=GND)
#define MOTOR_CURRENT_MA  800     // 800mA RMS current (adjustable)

// ── Stepper timing ───────────────────────────────────────────────
#define STEP_DELAY_US   500   // microseconds between step pulses (speed control)
#define PULSE_WIDTH_US  10    // pulse HIGH width in microseconds

// ── Mechanical constants ─────────────────────────────────────────
#define STEPS_PER_MM    80    // 3200 microsteps/rev ÷ 40 mm/rev

// ── Default speed ────────────────────────────────────────────────
#define DEFAULT_FEED_RATE  1000   // mm/min default feed rate
#define MAX_FEED_RATE      5000   // mm/min maximum feed rate

// ── Public API ────────────────────────────────────────────────────

/**
 * Configure GPIO pins for stepper control.
 * Must be called once in setup().
 */
void initMotion();

/**
 * Drive a stepper motor a given number of steps.
 *
 * @param stepPin  GPIO pin for STEP signal
 * @param dirPin   GPIO pin for DIR signal
 * @param dir      1 = positive direction, 0 = negative
 * @param steps    number of microsteps to execute
 *
 * Honors the stop flag — will abort mid-move if setStopFlag(true) is called.
 */
void stepMotor(uint8_t stepPin, uint8_t dirPin, int dir, int steps);

/**
 * Move both axes to an absolute position using Bresenham line algorithm.
 * Provides synchronized diagonal movement.
 *
 * @param x_mm     target X position in millimeters
 * @param y_mm     target Y position in millimeters
 * @param feed_rate  speed in mm/min (0 = rapid / max speed)
 */
void moveToXY(float x_mm, float y_mm, float feed_rate);

/**
 * Move relative from current position.
 *
 * @param dx_mm    delta X in millimeters
 * @param dy_mm    delta Y in millimeters
 * @param feed_rate  speed in mm/min (0 = rapid / max speed)
 */
void moveRelative(float dx_mm, float dy_mm, float feed_rate);

/**
 * Return to origin (0, 0).
 */
void home();

/**
 * Get current position.
 */
float getPositionX();
float getPositionY();

/**
 * Set the emergency stop flag.
 * When true, any in-progress move will abort.
 */
void setStopFlag(bool flag);

/**
 * Read the current stop flag state.
 */
bool getStopFlag();

#endif // MOTION_H
