// Laser / LED intensity via LEDC PWM.
//
// The host sends a plain 0..LASER_VALUE_MAX value and expects the firmware to
// do the PWM, which is exactly what ImSwitch and acquire_lsft.py assume
// ("laser power is passed straight through; PWM generation is in firmware").
#pragma once

#include <Arduino.h>
#include "config.h"

void illuminationBegin();

// id is 1-based, matching LASERid in the protocol.
void laserSet(uint8_t id, uint16_t value);
uint16_t laserGet(uint8_t id);
void laserAllOff();

// Re-route a laser channel to a different pin at runtime (/laser_set).
bool laserSetPin(uint8_t id, int8_t pin);
