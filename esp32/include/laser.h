#ifndef LASER_H
#define LASER_H

#include <Arduino.h>

// ── Hardware Configuration ─────────────────────────────────────────
#define LASER_PWM_PIN       25
#define LASER_PWM_CHANNEL   0
#define LASER_PWM_FREQ      5000  // 5 kHz as requested
#define LASER_PWM_RES       10    // 10-bit resolution (0-1023)

// ── Public API ────────────────────────────────────────────────────

/**
 * Initializes the laser pin and PWM hardware.
 * Ensures the laser starts in a strictly OFF state.
 */
void initLaser();

/**
 * Sets the laser power based on standard G-Code S values (S0 to S1000).
 * Maps 0-1000 to the 10-bit hardware PWM range (0-1023).
 */
void setLaserPower(float s_value);

/**
 * Immediately cuts power to the laser (duty cycle = 0).
 */
void turnLaserOff();

/**
 * Returns true if the laser is currently active (duty cycle > 0).
 */
bool isLaserOn();

#endif // LASER_H
