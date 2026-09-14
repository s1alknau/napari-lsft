#include "motion.h"

#include <math.h>

#include "soc/gpio_struct.h"
#include "soc/rtc_io_reg.h"

// The ESP32 does not save the FPU registers across an interrupt, so the ISR
// below is strictly integer. Everything that needs floating point (waveform
// tables, ramp lengths) is precomputed here, in task context, and handed to
// the ISR as plain integers.

static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
static hw_timer_t *tickTimer = nullptr;

// --------------------------------------------------------------------------
// ISR-safe pin access
// --------------------------------------------------------------------------
// digitalWrite() and dacWrite() take spinlocks and live in flash, so neither
// may be called from the ISR. Both banks of the GPIO matrix and the two DAC
// pads are memory mapped, and a raw register write is all that is needed.

static inline IRAM_ATTR void gpioApply(uint32_t setLo, uint32_t clrLo,
                                       uint32_t setHi, uint32_t clrHi) {
    if (clrLo) GPIO.out_w1tc = clrLo;
    if (setLo) GPIO.out_w1ts = setLo;
    if (clrHi) GPIO.out1_w1tc.val = clrHi;
    if (setHi) GPIO.out1_w1ts.val = setHi;
}

static inline IRAM_ATTR bool gpioRead(int8_t pin) {
    if (pin < 32) return (GPIO.in >> pin) & 1U;
    return (GPIO.in1.val >> (pin - 32)) & 1U;
}

#if GALVO_ENABLED
static inline IRAM_ATTR void dacFast(uint8_t ch, uint8_t value) {
    if (ch == 0) {
        REG_SET_FIELD(RTC_IO_PAD_DAC1_REG, RTC_IO_PDAC1_DAC, value);
    } else {
        REG_SET_FIELD(RTC_IO_PAD_DAC2_REG, RTC_IO_PDAC2_DAC, value);
    }
}
#endif

static void maskFor(int8_t pin, uint32_t &lo, uint32_t &hi) {
    if (pin < 0) return;
    if (pin < 32) lo |= (1UL << pin);
    else hi |= (1UL << (pin - 32));
}

// --------------------------------------------------------------------------
// Galvo (only built when GALVO_ENABLED; the UC2 galvo board owns the sweep)
// --------------------------------------------------------------------------
// One 256-entry table per channel holds the finished DAC codes, so the ISR is
// a phase-accumulator add and a table lookup. Rebuilding the table is how
// amplitude, offset, waveform and inversion are applied.
#if GALVO_ENABLED
#define GALVO_LUT_LEN 256

struct GalvoState {
    volatile bool running = false;
    volatile uint32_t phase = 0;
    volatile uint32_t inc = 0;
    volatile uint8_t lut[GALVO_LUT_LEN];
    float frequency = 0.0f;
};

static GalvoState galvo[GALVO_N_CHANNELS];

static uint8_t galvoCode(float unit, float amplitude, float offset) {
    // unit is the waveform in [-1, 1]; 128 is the 1.65 V centre of the span.
    float v = 128.0f + offset * 127.0f + amplitude * 127.0f * unit;
    if (v < 0.0f) v = 0.0f;
    if (v > 255.0f) v = 255.0f;
    return (uint8_t)lrintf(v);
}

void galvoConfigure(uint8_t channel, float frequency, float amplitude,
                    float offset, float phase, bool invert, GalvoWave wave) {
    if (channel < 1 || channel > GALVO_N_CHANNELS) return;
    GalvoState &g = galvo[channel - 1];

    if (amplitude < 0.0f) amplitude = 0.0f;
    if (amplitude > GALVO_MAX_AMPLITUDE) amplitude = GALVO_MAX_AMPLITUDE;
    if (offset < -1.0f) offset = -1.0f;
    if (offset > 1.0f) offset = 1.0f;
    if (frequency < 0.0f) frequency = 0.0f;
    if (frequency > GALVO_MAX_FREQ_HZ) frequency = GALVO_MAX_FREQ_HZ;

    // Stop before rewriting the table the ISR is reading from.
    portENTER_CRITICAL(&mux);
    g.running = false;
    portEXIT_CRITICAL(&mux);

    for (int i = 0; i < GALVO_LUT_LEN; ++i) {
        float t = (float)i / (float)GALVO_LUT_LEN; // 0..1 within one period
        float unit;
        switch (wave) {
            case GALVO_SINE:
                unit = sinf(2.0f * (float)M_PI * t);
                break;
            case GALVO_SAWTOOTH:
                unit = 2.0f * t - 1.0f;
                break;
            case GALVO_TRIANGLE:
            default:
                unit = (t < 0.5f) ? (4.0f * t - 1.0f) : (3.0f - 4.0f * t);
                break;
        }
        if (invert) unit = -unit;
        g.lut[i] = galvoCode(unit, amplitude, offset);
    }

    g.frequency = frequency;
    uint32_t inc = (uint32_t)((double)frequency * 4294967296.0 / (double)TICK_HZ);
    uint32_t start =
        (uint32_t)((double)fmodf(phase, 360.0f) / 360.0 * 4294967296.0);

    portENTER_CRITICAL(&mux);
    g.inc = inc;
    g.phase = start;
    g.running = (frequency > 0.0f);
    portEXIT_CRITICAL(&mux);

    if (frequency <= 0.0f) {
        // Sweep off: park the mirror at the DC offset rather than wherever the
        // last sample happened to leave it.
        dacFast(channel - 1, galvoCode(0.0f, 0.0f, offset));
    }
}

void galvoStop(uint8_t channel) {
    if (channel < 1 || channel > GALVO_N_CHANNELS) return;
    portENTER_CRITICAL(&mux);
    galvo[channel - 1].running = false;
    portEXIT_CRITICAL(&mux);
    galvo[channel - 1].frequency = 0.0f;
    dacFast(channel - 1, 128);
}

float galvoFrequency(uint8_t channel) {
    if (channel < 1 || channel > GALVO_N_CHANNELS) return 0.0f;
    return galvo[channel - 1].frequency;
}
#endif // GALVO_ENABLED

// --------------------------------------------------------------------------
// Steppers
// --------------------------------------------------------------------------
// Half-step sequence for a 28BYJ-48 on a ULN2003, as bits IN1..IN4.
// If the motor only buzzes instead of turning, the coil pair order is wrong:
// swap pin[1] and pin[2] of that axis in config.h.
static const uint8_t HALFSTEP[8] = {0b1000, 0b1100, 0b0100, 0b0110,
                                    0b0010, 0b0011, 0b0001, 0b1001};

struct AxisState {
    // Precomputed GPIO masks; index 0..7 for the unipolar phases.
    uint32_t setLo[8], clrLo[8], setHi[8], clrHi[8];
    uint32_t stepSetLo = 0, stepSetHi = 0, stepClrLo = 0, stepClrHi = 0;
    uint32_t dirSetLo = 0, dirSetHi = 0, dirClrLo = 0, dirClrHi = 0;
    uint32_t enSetLo = 0, enSetHi = 0, enClrLo = 0, enClrHi = 0;
    uint32_t offLo = 0, offHi = 0; // all coils de-energised

    volatile bool running = false;
    volatile bool forever = false;
    volatile bool homing = false;
    volatile bool done = false;
    volatile bool energised = false;
    volatile bool pendingStepLow = false;
    volatile int8_t dir = 1;
    volatile uint8_t phaseIdx = 0;
    volatile int32_t position = 0;
    volatile uint32_t remaining = 0;
    volatile uint32_t acc = 0;
    volatile uint32_t inc = 0;
    volatile uint32_t incMin = 0;
    volatile uint32_t incTarget = 0;
    volatile uint32_t incStep = 0;
    volatile uint32_t decelSteps = 0;
    volatile uint32_t holdTicks = 0;
    volatile uint32_t timeoutTicks = 0;
    volatile int8_t endstopPin = -1;
    volatile bool endstopActiveHigh = false;
};

static AxisState axes[N_AXES];

static inline uint32_t incFor(uint32_t stepsPerSecond) {
    if (stepsPerSecond == 0) return 0;
    // Cap at half the tick rate: one step every other tick is the fastest a
    // STEP pulse can be emitted with a full low phase in between.
    uint32_t maxRate = TICK_HZ / 2;
    if (stepsPerSecond > maxRate) stepsPerSecond = maxRate;
    return (uint32_t)(((uint64_t)stepsPerSecond << 32) / (uint64_t)TICK_HZ);
}

static inline IRAM_ATTR void energise(AxisState &s, const AxisConfig &c,
                                      uint8_t phase) {
    if (c.kind == AXIS_KIND_UNIPOLAR) {
        gpioApply(s.setLo[phase], s.clrLo[phase], s.setHi[phase], s.clrHi[phase]);
    }
    s.energised = true;
}

static inline IRAM_ATTR void deEnergise(AxisState &s, const AxisConfig &c) {
    if (c.kind == AXIS_KIND_UNIPOLAR) {
        gpioApply(0, s.offLo, 0, s.offHi);
    } else if (c.kind == AXIS_KIND_STEPDIR && c.pin[2] >= 0) {
        // Driver enable inputs are active low.
        gpioApply(s.enSetLo, 0, s.enSetHi, 0);
    }
    s.energised = false;
}

static inline IRAM_ATTR void emitStep(AxisState &s, const AxisConfig &c) {
    if (c.kind == AXIS_KIND_UNIPOLAR) {
        s.phaseIdx = (uint8_t)((s.phaseIdx + (s.dir > 0 ? 1 : 7)) & 7);
        energise(s, c, s.phaseIdx);
    } else {
        gpioApply(s.stepSetLo, 0, s.stepSetHi, 0);
        s.pendingStepLow = true;
    }
    s.position += s.dir;
}

// --------------------------------------------------------------------------
// The tick
// --------------------------------------------------------------------------
static void IRAM_ATTR onTick() {
#if GALVO_ENABLED
    // Galvo first: the sheet quality is what depends on steady timing.
    for (uint8_t ch = 0; ch < GALVO_N_CHANNELS; ++ch) {
        GalvoState &g = galvo[ch];
        if (!g.running) continue;
        g.phase += g.inc;
        dacFast(ch, g.lut[g.phase >> 24]);
    }
#endif

    for (uint8_t i = 0; i < N_AXES; ++i) {
        AxisState &s = axes[i];
        const AxisConfig &c = AXIS_CFG[i];
        if (c.kind == AXIS_KIND_OFF) continue;

        if (s.pendingStepLow) { // end the STEP pulse started last tick
            gpioApply(0, s.stepClrLo, 0, s.stepClrHi);
            s.pendingStepLow = false;
        }

        if (!s.running) {
            if (s.energised && s.holdTicks) {
                if (--s.holdTicks == 0) deEnergise(s, c);
            }
            continue;
        }

        if (s.homing) {
            bool hit = gpioRead(s.endstopPin) == s.endstopActiveHigh;
            bool timedOut = s.timeoutTicks && (--s.timeoutTicks == 0);
            if (hit || timedOut) {
                s.running = false;
                s.homing = false;
                s.forever = false;
                s.done = true;
                if (hit) s.position = 0;
                s.holdTicks = (uint32_t)((uint64_t)c.holdMs * TICK_HZ / 1000);
                continue;
            }
        }

        // Trapezoidal ramp, integer only.
        if (!s.forever && !s.homing && s.remaining <= s.decelSteps) {
            s.inc = (s.inc > s.incMin + s.incStep) ? s.inc - s.incStep : s.incMin;
        } else if (s.inc < s.incTarget) {
            uint32_t next = s.inc + s.incStep;
            s.inc = (next > s.incTarget || next < s.inc) ? s.incTarget : next;
        }

        uint32_t prev = s.acc;
        s.acc += s.inc;
        if (s.acc >= prev) continue; // no wrap -> no step on this tick

        emitStep(s, c);

        if (!s.forever && !s.homing) {
            if (--s.remaining == 0) {
                s.running = false;
                s.done = true;
                s.holdTicks = (uint32_t)((uint64_t)c.holdMs * TICK_HZ / 1000);
            }
        }
    }
}

// --------------------------------------------------------------------------
// Setup
// --------------------------------------------------------------------------
void motionBegin() {
    for (uint8_t i = 0; i < N_AXES; ++i) {
        AxisState &s = axes[i];
        const AxisConfig &c = AXIS_CFG[i];
        s.endstopPin = c.endstopPin;
        s.endstopActiveHigh = c.endstopActiveHigh;

        if (c.kind == AXIS_KIND_UNIPOLAR) {
            for (uint8_t p = 0; p < 4; ++p) {
                if (c.pin[p] < 0) continue;
                pinMode(c.pin[p], OUTPUT);
                digitalWrite(c.pin[p], LOW);
                maskFor(c.pin[p], s.offLo, s.offHi);
            }
            for (uint8_t ph = 0; ph < 8; ++ph) {
                for (uint8_t p = 0; p < 4; ++p) {
                    bool on = HALFSTEP[ph] & (0b1000 >> p);
                    if (on) maskFor(c.pin[p], s.setLo[ph], s.setHi[ph]);
                    else maskFor(c.pin[p], s.clrLo[ph], s.clrHi[ph]);
                }
            }
        } else if (c.kind == AXIS_KIND_STEPDIR) {
            pinMode(c.pin[0], OUTPUT);
            digitalWrite(c.pin[0], LOW);
            maskFor(c.pin[0], s.stepSetLo, s.stepSetHi);
            maskFor(c.pin[0], s.stepClrLo, s.stepClrHi);
            pinMode(c.pin[1], OUTPUT);
            digitalWrite(c.pin[1], LOW);
            maskFor(c.pin[1], s.dirSetLo, s.dirSetHi);
            maskFor(c.pin[1], s.dirClrLo, s.dirClrHi);
            if (c.pin[2] >= 0) {
                pinMode(c.pin[2], OUTPUT);
                digitalWrite(c.pin[2], HIGH); // active low -> start disabled
                maskFor(c.pin[2], s.enSetLo, s.enSetHi);
                maskFor(c.pin[2], s.enClrLo, s.enClrHi);
            }
        }

        if (c.endstopPin >= 0) {
            // GPIO34..39 have no internal pull-ups; those need external ones.
            pinMode(c.endstopPin, c.endstopPin >= 34 ? INPUT : INPUT_PULLUP);
        }
    }

#if GALVO_ENABLED
    // Route both DAC pads and park them mid-span before the ISR starts.
    dacWrite(25, 128);
    dacWrite(26, 128);
    for (uint8_t ch = 0; ch < GALVO_N_CHANNELS; ++ch) {
        for (int i = 0; i < GALVO_LUT_LEN; ++i) galvo[ch].lut[i] = 128;
    }
#endif

#if ESP_ARDUINO_VERSION_MAJOR >= 3
    tickTimer = timerBegin(TICK_HZ);
    timerAttachInterrupt(tickTimer, &onTick);
    timerAlarm(tickTimer, 1, true, 0);
#else
    tickTimer = timerBegin(0, 80, true); // 80 MHz / 80 = 1 MHz
    timerAttachInterrupt(tickTimer, &onTick, true);
    timerAlarmWrite(tickTimer, 1000000UL / TICK_HZ, true);
    timerAlarmEnable(tickTimer);
#endif
}

// --------------------------------------------------------------------------
// Commands
// --------------------------------------------------------------------------
static void primeMove(uint8_t axis, int32_t steps, uint32_t speed,
                      uint32_t accel, bool forever, bool enable,
                      bool homing = false) {
    AxisState &s = axes[axis];
    const AxisConfig &c = AXIS_CFG[axis];

    if (speed == 0) speed = c.startSpeed;
    if (speed > c.maxSpeed) speed = c.maxSpeed; // the host happily asks for 15000
    if (accel == 0) accel = c.accel;

    int8_t dir = (steps >= 0) ? 1 : -1;
    if (c.invert) dir = -dir;
    uint32_t distance = (uint32_t)((steps >= 0) ? steps : -(int64_t)steps);

    uint32_t startSpeed = min(c.startSpeed, speed);
    uint32_t incTarget = incFor(speed);
    uint32_t incMin = incFor(startSpeed);
    uint32_t incStep = incFor(accel) / TICK_HZ;
    if (incStep == 0) incStep = 1;

    // Steps needed to ramp from startSpeed to speed: (v^2 - v0^2) / (2a).
    float v = (float)speed, v0 = (float)startSpeed;
    uint32_t ramp =
        (uint32_t)fmaxf(0.0f, (v * v - v0 * v0) / (2.0f * (float)accel));
    if (!forever && ramp > distance / 2) ramp = distance / 2;

    portENTER_CRITICAL(&mux);
    s.dir = dir;
    s.forever = forever;
    s.homing = homing;
    s.remaining = distance;
    s.acc = 0;
    s.inc = incMin;
    s.incMin = incMin;
    s.incTarget = incTarget;
    s.incStep = incStep;
    s.decelSteps = ramp;
    s.holdTicks = 0;
    s.done = false;
    if (c.kind == AXIS_KIND_STEPDIR) {
        if (dir > 0) gpioApply(s.dirSetLo, 0, s.dirSetHi, 0);
        else gpioApply(0, s.dirClrLo, 0, s.dirClrHi);
        if (enable && c.pin[2] >= 0) gpioApply(0, s.enClrLo, 0, s.enClrHi);
    } else if (enable) {
        energise(s, c, s.phaseIdx);
    }
    s.running = true;
    portEXIT_CRITICAL(&mux);
}

bool stepperMove(uint8_t axis, const MoveRequest &req) {
    if (axis >= N_AXES) return false;
    const AxisConfig &c = AXIS_CFG[axis];
    if (c.kind == AXIS_KIND_OFF) return false;

    if (req.isForever) {
        // Only the sign of position is read here; the run has no end point.
        primeMove(axis, (req.position >= 0) ? 1 : -1, req.speed, req.accel, true,
                  req.enable);
        return true;
    }

    int32_t steps = req.position;
    if (req.isAbsolute) {
        portENTER_CRITICAL(&mux);
        int32_t current = axes[axis].position;
        portEXIT_CRITICAL(&mux);
        steps = req.position - current;
    }
    if (steps == 0) return false;

    primeMove(axis, steps, req.speed, req.accel, false, req.enable);
    return true;
}

bool stepperHome(uint8_t axis, uint32_t speed, int8_t direction,
                 bool endstopActiveHigh, uint32_t timeoutMs) {
    if (axis >= N_AXES) return false;
    const AxisConfig &c = AXIS_CFG[axis];
    if (c.kind == AXIS_KIND_OFF || c.endstopPin < 0) return false;

    // The endstop terms have to be in place before the axis starts running,
    // so they are set first and primeMove arms homing inside its own critical
    // section rather than leaving a window where the ISR steps unguarded.
    portENTER_CRITICAL(&mux);
    axes[axis].endstopActiveHigh = endstopActiveHigh;
    axes[axis].timeoutTicks =
        timeoutMs ? (uint32_t)((uint64_t)timeoutMs * TICK_HZ / 1000) : 0;
    portEXIT_CRITICAL(&mux);

    primeMove(axis, direction >= 0 ? 1 : -1, speed, c.accel, true, true, true);
    return true;
}

void stepperStop(uint8_t axis) {
    if (axis >= N_AXES) return;
    AxisState &s = axes[axis];
    portENTER_CRITICAL(&mux);
    bool wasRunning = s.running;
    s.running = false;
    s.forever = false;
    s.homing = false;
    s.remaining = 0;
    if (wasRunning) s.done = true; // a blocking host may still be waiting
    s.holdTicks = (uint32_t)((uint64_t)AXIS_CFG[axis].holdMs * TICK_HZ / 1000);
    portEXIT_CRITICAL(&mux);
}

int32_t stepperPosition(uint8_t axis) {
    if (axis >= N_AXES) return 0;
    portENTER_CRITICAL(&mux);
    int32_t p = axes[axis].position;
    portEXIT_CRITICAL(&mux);
    return p;
}

void stepperSetPosition(uint8_t axis, int32_t position) {
    if (axis >= N_AXES) return;
    portENTER_CRITICAL(&mux);
    axes[axis].position = position;
    portEXIT_CRITICAL(&mux);
}

bool stepperBusy(uint8_t axis) {
    if (axis >= N_AXES) return false;
    return axes[axis].running;
}

bool anyStepperBusy() {
    for (uint8_t i = 0; i < N_AXES; ++i)
        if (axes[i].running) return true;
    return false;
}

bool stepperTakeDone(uint8_t axis) {
    if (axis >= N_AXES) return false;
    portENTER_CRITICAL(&mux);
    bool d = axes[axis].done;
    axes[axis].done = false;
    portEXIT_CRITICAL(&mux);
    return d;
}
