// LSFT firmware -- light-sheet fluorescence tomograph, ESP32-WROOM-32.
//
// Speaks the UC2-REST serial protocol, so it is driven unchanged by
// napari_lsft._hardware.ESP32Controller, by ImSwitch's ESP32 managers, and by
// acquisition/acquire_lsft.py. See ../README.md for the wiring and protocol.

#include <Arduino.h>

#include "config.h"
#include "illumination.h"
#include "motion.h"
#include "protocol.h"

void setup() {
    Serial.begin(LSFT_SERIAL_BAUD);

    // Illumination first, so the laser is driven to zero before anything else
    // can go wrong: a floating gate on a laser driver is not a safe state.
    illuminationBegin();
    motionBegin();
    protocolBegin();

    Serial.println(LSFT_FW_NAME " " LSFT_FW_VERSION " ready");
}

void loop() {
    // All timing-critical work happens in the tick ISR; this loop only moves
    // JSON in and out, which keeps the rotation immune to however long a
    // serial write happens to take.
    protocolPoll();
}
