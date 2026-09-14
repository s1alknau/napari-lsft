// Compile-time configuration of the LSFT rig: identity, pin map, motor
// characteristics and safety limits. This is the only file that has to be
// touched when the wiring changes.
#pragma once

#include <Arduino.h>

// --------------------------------------------------------------------------
// Identity (reported by /state_get, shown by ESP32Controller.identify())
// --------------------------------------------------------------------------
#define LSFT_FW_NAME "LSFT-ESP32"
#define LSFT_FW_VERSION "1.0.0"
#define LSFT_SERIAL_BAUD 115200

// --------------------------------------------------------------------------
// Axis numbering -- fixed by the UC2-REST protocol (Motor.xyztTo1230):
// A=0, X=1, Y=2, Z=3. A is the capillary rotation axis of the tomograph.
// --------------------------------------------------------------------------
enum : uint8_t { AXIS_A = 0, AXIS_X = 1, AXIS_Y = 2, AXIS_Z = 3, N_AXES = 4 };

enum AxisKind : uint8_t {
    AXIS_KIND_OFF = 0,      // not populated; commands are accepted and ignored
    AXIS_KIND_UNIPOLAR = 1, // 28BYJ-48 style, 4 coil pins via a ULN2003
    AXIS_KIND_STEPDIR = 2,  // A4988/DRV8825/TMC220x style STEP+DIR
};

struct AxisConfig {
    AxisKind kind;
    // UNIPOLAR: pin[0..3] = IN1..IN4 of the ULN2003 board.
    // STEPDIR : pin[0] = STEP, pin[1] = DIR, pin[2] = ENABLE (-1 = none).
    int8_t pin[4];
    bool invert;         // flip the sense of positive steps
    uint32_t maxSpeed;   // steps/s ceiling -- the host asks for far more
    uint32_t startSpeed; // steps/s the ramp starts/ends at (no jerk limit)
    uint32_t accel;      // steps/s^2 used when the host sends no "accel"
    uint32_t holdMs;     // keep coils energised this long after a move
    int8_t endstopPin;   // -1 = no endstop -> /home_act is refused
    bool endstopActiveHigh;
};

// --------------------------------------------------------------------------
// Pin map
// --------------------------------------------------------------------------
// Chosen to avoid every pin that is not freely usable on an ESP32-WROOM-32:
//   GPIO 0/2/5/12/15  strapping pins (affect boot mode)
//   GPIO 6..11        internal SPI flash
//   GPIO 1/3          UART0 = the USB serial link the host talks over
//   GPIO 34..39       input only, and *without* internal pull-ups
//
// A axis: 28BYJ-48 (5 V unipolar) on a ULN2003 driver board.
//   The ULN2003 board is powered from 5 V (VIN/USB-5V), NOT from 3V3, and its
//   ground must be tied to the ESP32 ground. Its IN1..IN4 inputs sit behind
//   base resistors, so 3.3 V logic drives them fine.
//
//   As wired on this rig: 26 -> IN1, 25 -> IN2, 33 -> IN3, 32 -> IN4.
//
//   That takes GPIO25/26, which are also the two DAC pins, so the on-board
//   galvo fallback (GALVO_ENABLED) cannot coexist with this axis. It is not
//   meant to: the sheet sweep lives on the UC2 galvo board. If the fallback is
//   ever needed, the A axis has to move to four of {4, 13, 14, 21, 22, 23, 27}
//   first.
static const AxisConfig AXIS_CFG[N_AXES] = {
    // A -- capillary rotation
    {AXIS_KIND_UNIPOLAR,
     {26, 25, 33, 32},
     /*invert*/ false,
     /*maxSpeed*/ 900,    // 28BYJ-48 stalls well below this; see README
     /*startSpeed*/ 120,
     /*accel*/ 2000,
     /*holdMs*/ 1500,     // the 1:64 gearbox holds position on its own
     /*endstopPin*/ -1,
     /*endstopActiveHigh*/ false},
    // X/Y/Z -- optional translation stage (STEP/DIR), not populated here.
    //
    // ENABLE is -1 on all three: A4988/DRV8825/TMC220x hold EN low internally
    // and are enabled by default, so no pin is spent on it. With the lasers on
    // 16-19 and the A axis on 32/33/25/26, exactly 13, 14, 27, 4, 21, 22, 23
    // are left -- enough for three STEP/DIR pairs with one pin to spare.
    {AXIS_KIND_OFF, {13, 14, -1, -1}, false, 10000, 400, 20000, 0, 34, false},
    // Y
    {AXIS_KIND_OFF, {22, 21, -1, -1}, false, 10000, 400, 20000, 0, 35, false},
    // Z
    {AXIS_KIND_OFF, {23, 4, -1, -1}, false, 10000, 400, 20000, 0, 36, false},
};

// --------------------------------------------------------------------------
// Illumination (LEDC PWM). LASERid in the protocol is 1-based.
// --------------------------------------------------------------------------
// Four wavelengths on this rig. LASERid 1..4 map to these pins in order.
#define LASER_N_CHANNELS 4
static const int8_t LASER_PIN[LASER_N_CHANNELS] = {16, 17, 18, 19};
#define LASER_PWM_FREQ_HZ 20000 // above camera exposure and audible range
#define LASER_PWM_BITS 10       // 80 MHz / 2^10 = 78 kHz ceiling, fine at 20 k
#define LASER_VALUE_MAX 255     // host range (matches ImSwitch valueRangeMax)

// Dead-man switch: if no command arrives for this many milliseconds while a
// laser channel is on, switch it off. 0 disables it. Off by default because a
// single long rotation can legitimately go minutes without a command, and
// killing the illumination mid-scan silently corrupts a stack -- turn it on
// (e.g. 300000) for unattended operation.
#define LASER_WATCHDOG_MS 0

// --------------------------------------------------------------------------
// Galvo / light sheet -- not driven by this board
// --------------------------------------------------------------------------
// The sheet sweep lives on the UC2 galvo board with its own Seeed Studio
// controller, so GPIO25/26 stay free here and /dac_act is acknowledged with
// "return":0 instead of being acted on. The generator (triangle/sine/sawtooth
// on the two DAC channels) is still in motion.cpp: set this to 1 to bring it
// back, and the two DAC pins become the galvo output again.
#define GALVO_ENABLED 0

#if GALVO_ENABLED
#define GALVO_N_CHANNELS 2 // DAC1 = GPIO25, DAC2 = GPIO26
// Hard ceiling on the sweep amplitude, as a fraction of the 0..3.3 V DAC span.
// Lower this while aligning so a wrong host value cannot slam the mirror.
#define GALVO_MAX_AMPLITUDE 1.0f
#define GALVO_MAX_FREQ_HZ 2000.0f
#else
#define GALVO_N_CHANNELS 0
#endif

// --------------------------------------------------------------------------
// Control loop
// --------------------------------------------------------------------------
// One hardware timer ISR drives every stepper (and the galvo waveform, when
// that is enabled). 20 kHz -> 50 us resolution, so ~10 kHz maximum STEP rate
// and a 50 us granularity on the coil sequence of a unipolar motor.
#define TICK_HZ 20000

#define JSON_LINE_MAX 1024 // longest command line accepted from the host
