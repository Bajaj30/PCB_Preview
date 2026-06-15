/*
 * laser.cpp — Hardware PWM control for the Laser
 *
 * Configured for 5000Hz via ledc peripheral.
 * Maps standard G-Code S values (0-1000) to 10-bit duty cycle (0-1023).
 */

#include "laser.h"

static bool laserActive = false;

void initLaser() {
  // Setup PWM channel
  ledcSetup(LASER_PWM_CHANNEL, LASER_PWM_FREQ, LASER_PWM_RES);

  // Attach pin to channel
  ledcAttachPin(LASER_PWM_PIN, LASER_PWM_CHANNEL);

  // Ensure laser is off at startup
  turnLaserOff();

  Serial.println("[laser] PWM initialized on GPIO 25");
  Serial.printf("[laser] Frequency: %d Hz, Res: %d-bit\n", LASER_PWM_FREQ,
                LASER_PWM_RES);
}

void setLaserPower(float s_value) {
  if (s_value <= 0) {
    turnLaserOff();
    return;
  }

  // Cap maximum power at S1000
  if (s_value > 1000.0) {
    s_value = 1000.0;
  }

  // Map S (0-1000) to 10-bit PWM (0-1023)
  uint32_t duty = (uint32_t)((s_value / 1000.0) * 1023.0);

  ledcWrite(LASER_PWM_CHANNEL, duty);
  laserActive = true;

  // Uncomment for verbose debugging if needed:
  // Serial.printf("[laser] Power set to %.1f (PWM: %u)\n", s_value, duty);
}

void turnLaserOff() {
  ledcWrite(LASER_PWM_CHANNEL, 0);
  laserActive = false;
}

bool isLaserOn() { return laserActive; }
