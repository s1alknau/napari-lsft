#include "illumination.h"

static int8_t pins[LASER_N_CHANNELS];
static uint16_t values[LASER_N_CHANNELS];

static const uint32_t DUTY_MAX = (1UL << LASER_PWM_BITS) - 1;

static void attach(uint8_t idx) {
    if (pins[idx] < 0) return;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcAttach(pins[idx], LASER_PWM_FREQ_HZ, LASER_PWM_BITS);
#else
    ledcSetup(idx, LASER_PWM_FREQ_HZ, LASER_PWM_BITS);
    ledcAttachPin(pins[idx], idx);
#endif
}

static void writeDuty(uint8_t idx, uint32_t duty) {
    if (pins[idx] < 0) return;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWrite(pins[idx], duty);
#else
    ledcWrite(idx, duty);
#endif
}

void illuminationBegin() {
    for (uint8_t i = 0; i < LASER_N_CHANNELS; ++i) {
        pins[i] = LASER_PIN[i];
        values[i] = 0;
        attach(i);
        writeDuty(i, 0);
    }
}

void laserSet(uint8_t id, uint16_t value) {
    if (id < 1 || id > LASER_N_CHANNELS) return;
    uint8_t idx = id - 1;
    if (value > LASER_VALUE_MAX) value = LASER_VALUE_MAX;
    values[idx] = value;
    writeDuty(idx, (uint32_t)value * DUTY_MAX / LASER_VALUE_MAX);
}

uint16_t laserGet(uint8_t id) {
    if (id < 1 || id > LASER_N_CHANNELS) return 0;
    return values[id - 1];
}

void laserAllOff() {
    for (uint8_t i = 0; i < LASER_N_CHANNELS; ++i) {
        values[i] = 0;
        writeDuty(i, 0);
    }
}

bool laserSetPin(uint8_t id, int8_t pin) {
    if (id < 1 || id > LASER_N_CHANNELS) return false;
    uint8_t idx = id - 1;
    if (pins[idx] >= 0) {
        writeDuty(idx, 0);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        ledcDetach(pins[idx]);
#else
        ledcDetachPin(pins[idx]);
#endif
    }
    pins[idx] = pin;
    attach(idx);
    laserSet(id, values[idx]);
    return true;
}
