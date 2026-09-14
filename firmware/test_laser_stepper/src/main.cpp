// Bench test: cycle the four laser wavelengths while the capillary rotates.
//
// Standalone -- no host, no UC2-REST, no JSON. Flash it, open the serial
// monitor at 115200, and watch. Each wavelength is switched on alone for
// ON_MS, then the next one takes over, round and round; the A axis turns
// continuously the whole time.
//
// This is a wiring/sanity check, not the instrument firmware. Flash the real
// one back with:  cd firmware && pio run -t upload
//
//     >>> CHECK THE PIN MAP BELOW against how your lasers are actually wired.
//     The board prints it at startup so you can confirm it on the monitor.

#include <Arduino.h>

// --------------------------------------------------------------------------
// Configuration -- the only part worth editing
// --------------------------------------------------------------------------

// The four laser channels, in the order they will be cycled.
static const int LASER_PIN[4] = {16, 17, 18, 19};
static const char *LASER_NAME[4] = {"laser 1", "laser 2", "laser 3", "laser 4"};

// 0-255. Kept off full scale so a first power-up is not the brightest it can
// be; raise it to 255 if a diode stays below its lasing threshold.
static const uint8_t LASER_LEVEL = 128;

static const uint32_t ON_MS = 5000; // dwell per wavelength

// A axis: 28BYJ-48 on a ULN2003, IN1..IN4 in that order. Same pins as the
// real firmware. As wired on this rig: 26->IN1, 25->IN2, 33->IN3, 32->IN4.
static const int MOTOR_PIN[4] = {26, 25, 33, 32};
static const uint32_t STEP_RATE = 400; // half-steps/s (~5.9 rpm)
static const int DIRECTION = +1;       // +1 or -1
static const uint32_t STEPS_PER_TURN = 4096; // half-step, for the turn counter

// Grace period before anything switches on, so the board is not firing a laser
// the instant it gets plugged in.
static const uint32_t START_DELAY_MS = 3000;

// --------------------------------------------------------------------------

#define PWM_FREQ 20000
#define PWM_BITS 10
static const uint32_t DUTY_MAX = (1UL << PWM_BITS) - 1;

// Half-step sequence, bits IN1..IN4. If the motor only buzzes instead of
// turning, swap MOTOR_PIN[1] and MOTOR_PIN[2].
static const uint8_t HALFSTEP[8] = {0b1000, 0b1100, 0b0100, 0b0110,
                                    0b0010, 0b0011, 0b0001, 0b1001};

static uint32_t stepIntervalUs;
static uint32_t lastStepUs;
static uint32_t lastSwitchMs;
static uint8_t phase = 0;
static uint8_t active = 0;
static int32_t stepCount = 0;
static bool autoCycle = true;

static void laserWrite(uint8_t idx, uint8_t value) {
    uint32_t duty = (uint32_t)value * DUTY_MAX / 255;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWrite(LASER_PIN[idx], duty);
#else
    ledcWrite(idx, duty);
#endif
}

static void selectLaser(uint8_t idx) {
    for (uint8_t i = 0; i < 4; ++i) laserWrite(i, i == idx ? LASER_LEVEL : 0);
    // Integer maths on purpose: float formatting in Serial.printf depends on
    // how newlib was built, and a bench readout should not be the thing that
    // surprises you.
    long hundredths = (long)stepCount * 100L / (long)STEPS_PER_TURN;
    Serial.printf("[%6lus] %s ON (GPIO %d) at %u/255 %s |  rotation %ld steps"
                  " = %ld.%02ld turns\n",
                  millis() / 1000UL, LASER_NAME[idx], LASER_PIN[idx],
                  LASER_LEVEL, autoCycle ? " " : "[held]", (long)stepCount,
                  hundredths / 100, labs(hundredths % 100));
}

static void stepMotor() {
    phase = (uint8_t)((phase + (DIRECTION > 0 ? 1 : 7)) & 7);
    for (uint8_t i = 0; i < 4; ++i)
        digitalWrite(MOTOR_PIN[i], (HALFSTEP[phase] & (0b1000 >> i)) ? HIGH : LOW);
    stepCount += DIRECTION;
}

void setup() {
    Serial.begin(115200);
    delay(200);

    for (uint8_t i = 0; i < 4; ++i) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
        ledcAttach(LASER_PIN[i], PWM_FREQ, PWM_BITS);
#else
        ledcSetup(i, PWM_FREQ, PWM_BITS);
        ledcAttachPin(LASER_PIN[i], i);
#endif
        laserWrite(i, 0);
    }
    for (uint8_t i = 0; i < 4; ++i) {
        pinMode(MOTOR_PIN[i], OUTPUT);
        digitalWrite(MOTOR_PIN[i], LOW);
    }

    Serial.println();
    Serial.println("LSFT laser + stepper bench test");
    Serial.printf("  lasers : GPIO %d, %d, %d, %d   %lu ms each at %u/255\n",
                  LASER_PIN[0], LASER_PIN[1], LASER_PIN[2], LASER_PIN[3],
                  (unsigned long)ON_MS, LASER_LEVEL);
    Serial.printf("  A axis : GPIO %d/%d/%d/%d (ULN2003), %lu half-steps/s,"
                  " direction %+d\n",
                  MOTOR_PIN[0], MOTOR_PIN[1], MOTOR_PIN[2], MOTOR_PIN[3],
                  (unsigned long)STEP_RATE, DIRECTION);
    Serial.println("  keys   : 1-4 hold one wavelength, 0 all off,"
                   " c resume cycling");
    Serial.printf("  starting in %lu ms -- reset the board to stop\n\n",
                  (unsigned long)START_DELAY_MS);
    delay(START_DELAY_MS);

    stepIntervalUs = 1000000UL / STEP_RATE;
    lastStepUs = micros();
    lastSwitchMs = millis();
    selectLaser(active);
}

// Keys from the serial monitor, so a single wavelength can be held and
// checked on its own instead of waiting for the cycle to come round.
static void handleKeys() {
    while (Serial.available()) {
        int c = Serial.read();
        if (c >= '1' && c <= '4') {
            autoCycle = false;
            active = (uint8_t)(c - '1');
            selectLaser(active);
        } else if (c == '0') {
            autoCycle = false;
            for (uint8_t i = 0; i < 4; ++i) laserWrite(i, 0);
            Serial.printf("[%6lus] all lasers OFF [held]\n", millis() / 1000UL);
        } else if (c == 'c' || c == 'C') {
            autoCycle = true;
            lastSwitchMs = millis();
            Serial.println("-- cycling again");
            selectLaser(active);
        }
    }
}

void loop() {
    uint32_t nowUs = micros();
    if (nowUs - lastStepUs >= stepIntervalUs) {
        lastStepUs += stepIntervalUs;
        stepMotor();
    }

    handleKeys();

    if (autoCycle && millis() - lastSwitchMs >= ON_MS) {
        lastSwitchMs += ON_MS;
        active = (uint8_t)((active + 1) & 3);
        selectLaser(active);
    }
}
