// Motion: everything that is driven from the periodic timer ISR.
//
// A single hardware timer at TICK_HZ services all four stepper axes -- and the
// galvo DAC waveform where that is enabled. Keeping them in one interrupt
// leaves the rotation free of the jitter competing timers would introduce.
#pragma once

#include <Arduino.h>
#include "config.h"

// --------------------------------------------------------------------------
// Galvo / light sheet -- compiled in only when GALVO_ENABLED is set. On this
// rig the sweep comes from the UC2 galvo board with its own controller.
// --------------------------------------------------------------------------
#if GALVO_ENABLED
enum GalvoWave : uint8_t {
    GALVO_TRIANGLE = 0, // default: constant sweep velocity -> even sheet
    GALVO_SINE = 1,     // what the stock UC2 firmware produces
    GALVO_SAWTOOTH = 2, // fly-back sweep
};

// frequency in Hz (0 stops the sweep and parks the mirror at the offset),
// amplitude and offset as a fraction of the full 0..3.3 V DAC span,
// phase in degrees. channel is 1-based (1 = GPIO25, 2 = GPIO26).
void galvoConfigure(uint8_t channel, float frequency, float amplitude,
                    float offset, float phase, bool invert, GalvoWave wave);
void galvoStop(uint8_t channel);
float galvoFrequency(uint8_t channel);
#endif

// --------------------------------------------------------------------------
// Steppers
// --------------------------------------------------------------------------
struct MoveRequest {
    int32_t position = 0;   // target (isAbsolute) or distance (relative), steps
    uint32_t speed = 0;     // steps/s, clamped to the axis maxSpeed
    uint32_t accel = 0;     // steps/s^2, 0 -> the axis default
    bool isAbsolute = false;
    bool isForever = false; // run until stopped; sign of speed gives direction
    bool enable = true;
};

// Returns true if the axis actually started moving.
bool stepperMove(uint8_t axis, const MoveRequest &req);
bool stepperHome(uint8_t axis, uint32_t speed, int8_t direction,
                 bool endstopActiveHigh, uint32_t timeoutMs);
void stepperStop(uint8_t axis);
int32_t stepperPosition(uint8_t axis);
void stepperSetPosition(uint8_t axis, int32_t position);
bool stepperBusy(uint8_t axis);
bool anyStepperBusy();

// Completion is flagged by the ISR and drained from loop(), because the
// "move finished" JSON must not be printed from interrupt context.
bool stepperTakeDone(uint8_t axis);

void motionBegin();
