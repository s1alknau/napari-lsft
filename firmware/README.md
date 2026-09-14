# LSFT firmware (ESP32)

Firmware for the light-sheet fluorescence tomograph: it drives the **capillary
rotation** and the **laser/LED**, and speaks the UC2-REST serial protocol so
the existing software stack talks to it unchanged.

The **light-sheet galvo is not driven from here** — the sweep comes from the
UC2 galvo scanner board with its own Seeed Studio controller, as a second
UC2-REST serial device. Its firmware is vendored alongside this one in
[`galvo/`](galvo/); the rig-side integration is in
[acquisition/galvo_scanner.md](../acquisition/galvo_scanner.md).
`/dac_act` is still accepted (and answered `"return":0`) so a host with
auto-galvo enabled does not stall. The internal-DAC generator is written and
compiles as a fallback; see
[Bringing the galvo back to the ESP32](#bringing-the-galvo-back-to-the-esp32).

```
napari-lsft plugin  ──┐
acquire_lsft.py     ──┼──► UC2-REST (JSON over USB serial) ──► this firmware ──► rig
ImSwitch managers   ──┘
```

Nothing on the Python side has to be adapted: `ESP32Controller` in
[`_hardware.py`](../src/napari_lsft/_hardware.py), ImSwitch's
`ESP32LEDLaserManager` / `ESP32StageManager` / `ESP32RotatorManager` and
[`acquire_lsft.py`](../acquisition/acquire_lsft.py) all issue exactly the
commands implemented here.

## Hardware

| Part | This rig |
|------|----------|
| MCU | ESP32-WROOM-32 NodeMCU (38 pin) on an OSOYOO breakout |
| Rotation (A axis) | 28BYJ-48, 5 V unipolar, on a ULN2003 driver board |
| Illumination | 4 laser wavelengths via PWM |
| Light sheet | UC2 galvo board + Seeed Studio controller — *separate, not this firmware* |

### Pin map

Defined in [`src/config.h`](src/config.h) — that is the only file to edit when
the wiring changes.

| Function | GPIO | Note |
|----------|------|------|
| A axis IN1 / IN2 / IN3 / IN4 | 26 / 25 / 33 / 32 | to the ULN2003 inputs, in that order |
| Laser 1–4 (`LASERid` 1…4) | 16 / 17 / 18 / 19 | 20 kHz PWM, one per wavelength |
| X / Y / Z STEP+DIR (optional) | 13,14 / 22,21 / 23,4 | `AXIS_KIND_OFF` by default, no ENABLE pin |
| Endstops X/Y/Z (optional) | 34 / 35 / 36 | **external 10 kΩ pull-up required** |
| *free* | 27 | one spare |

Every pin that is unsafe on a WROOM-32 is avoided: GPIO 0/2/5/12/15 are
strapping pins, 6–11 are the SPI flash, 1/3 are the USB serial link, and
34–39 are input-only *without* internal pull-ups.

### Wiring notes that matter

- **Power the ULN2003 board from 5 V, not from 3V3.** Use `VIN`/`5V` of the
  dev board, or better a separate 5 V supply — a 28BYJ-48 draws a few hundred
  mA per phase and can brown out the ESP32 over USB. The grounds must be tied
  together.
- The ULN2003 inputs sit behind base resistors, so 3.3 V logic drives them
  fine.
- **The lasers need drivers.** GPIO16-19 carry 3.3 V PWM signals for the
  modulation inputs of laser drivers, not for bare diodes.
- The A axis occupies **GPIO25/26, the two DAC pins**. The on-board galvo
  fallback below therefore cannot coexist with it — which is fine, because the
  sheet sweep lives on the UC2 galvo board. `4, 13, 14, 21, 22, 23, 27` are
  free if the axis ever has to move to free the DACs.

## Build and flash

[PlatformIO](https://platformio.org/) (the VS Code extension is enough):

```bash
cd firmware
pio run              # build
pio run -t upload    # build + flash
pio device monitor   # serial console at 115200
```

Only ArduinoJson is pulled in; PlatformIO fetches it on the first build.
Built and verified against `espressif32` 6.10 (Arduino core 2.0.17) and
ArduinoJson 7.4. The Arduino-core-3.x APIs for the timer and LEDC are handled
by `#if` guards, so a newer platform also compiles.

**Close ImSwitch and napari before flashing** — only one process may hold the
COM port.

## Verify on the bench

```bash
python tools/lsft_selftest.py --port COM5
```

This only needs `pyserial`. It checks identity, the laser PWM and a rotate-out /
rotate-back on the A axis, and prints PASS/FAIL per step — so a failure is
unambiguously firmware or wiring, before ImSwitch and napari are involved. The
galvo check is skipped unless the firmware was built with the sweep enabled.

Useful flags: `--skip-laser` (nothing eye-safe attached yet), `--steps` /
`--speed` (rotation test), `--debug` (dump the raw JSON traffic).

For the **galvo board** there is a separate tool,
[`tools/galvo_selftest.py`](tools/galvo_selftest.py), which walks the sweep
chain from the outside in — including a DC staircase you can put a meter on.
See [acquisition/galvo_scanner.md](../acquisition/galvo_scanner.md).

Both tools need the port to themselves: close napari, or disconnect in the
plugin, before running them.

### Bench test firmware (no host at all)

[`test_laser_stepper/`](test_laser_stepper/) is a separate, standalone sketch:
it cycles the four wavelengths, 5 s each, while the A axis turns continuously,
and reports each switch over the serial monitor. Useful for checking wiring
before any protocol is involved.

```bash
cd test_laser_stepper
pio run -t upload
pio device monitor
```

It **replaces** the LSFT firmware on the board. Put the real one back with
`cd firmware && pio run -t upload`. Pin map, dwell time and laser level are
constants at the top of its `src/main.cpp`.

## Calibrating the rotation axis

**This is the one number that decides whether a reconstruction is geometrically
correct.** The firmware counts half-steps; the host converts degrees to steps
with its own `steps_per_turn` setting, which must match the real gearbox.

The 28BYJ-48 runs in half-step mode here:

| | half-steps per full turn |
|---|---|
| Nominal (datasheet 1:64) | **4096** |
| Actual gear ratio 63.68395:1 | **≈ 4076** |

The datasheet ratio is rounded. Over a 180° scan the 0.5 % difference is about
0.9° of accumulated angle error — enough to blur a reconstruction. So:

1. Set the host to 4096 as a starting value:
   - napari plugin: the constant `STEPS_PER_TURN` in
     [`_hardware.py`](../src/napari_lsft/_hardware.py). It is deliberately not
     a GUI field — it is a property of the gearbox, not something to dial in
     per scan, and the acquisition plans in whole motor steps from it.
   - `acquire_lsft.py`: `--units-per-rev 4096`.
   - ImSwitch `ESP32RotatorManager`: `"stepsPerTurn": 4096`.
2. Then measure it: mark the capillary, command exactly 10 turns, and see how
   far the mark is off. Correct the value and repeat.

**Speed.** A 28BYJ-48 is slow. `maxSpeed` in `config.h` caps the A axis at 900
half-steps/s (~13 rpm) and the firmware silently clamps to it — UC2-REST asks
for 15000 steps/s by default, which this motor cannot follow. If steps are
lost, lower it: 300–500 half-steps/s is a safe working range. Every move is
ramped, so the motor is never started at full speed.

**Holding.** The coils are de-energised 1.5 s after a move (`holdMs`). The
1:64 gearbox holds the capillary mechanically, and this keeps the motor and
driver from cooking during a long scan. Set `holdMs` to a large value if your
mount does back-drive.

## Bringing the galvo back to the ESP32

Yes, this board can generate the sheet sweep itself — the code is here, behind
one switch in `config.h`:

```c
#define GALVO_ENABLED 1
```

GPIO25/26 then become the two sweep outputs and `/dac_act` drives them. Both
configurations are built and verified, so flipping the switch is not a
rewrite.

The waveform comes from a lookup table clocked by the same 20 kHz timer
interrupt that steps the motors, so the sweep is immune to serial traffic and
to whatever the rotation axis is doing.

| Parameter | Meaning here |
|-----------|--------------|
| `frequency` | sweep rate in Hz; **0 stops the sweep** and parks the mirror at the offset |
| `amplitude` | fraction of the full 0–3.3 V span, 0…1 |
| `offset` | DC shift of the sweep centre, −1…1 (0 = 1.65 V) |
| `phase` | start phase in degrees |
| `invert` | mirrors the waveform |
| `wave` | LSFT extension: 0 triangle (default), 1 sine, 2 sawtooth |

The default is a **triangle**, not the sine the stock UC2 firmware produces: a
sine dwells at its turning points and over-illuminates the edges of the sheet,
while a triangle sweeps at constant velocity and lights the field evenly.

### What that still needs in hardware

The ESP32 replaces the *waveform source*, not the galvo driver. A galvo needs a
servo amplifier that closes the loop on the mirror's position sensor — that is
what the UC2 galvo board does, and it does not go away. So:

```
ESP32 DAC (0–3.3 V) ──► op-amp: offset + gain ──► galvo driver command input (±5 V / ±10 V) ──► mirror
```

- GPIO25 is **single-ended, unbuffered, a few mA**. Never connect a mirror coil
  to it. Scale and buffer with an op-amp (TL072, OPA2134 or similar).
- Resolution is **8 bit — 256 positions per sweep**. For a continuously swept
  sheet where the camera integrates over many periods this is not visible. If
  you want finer, an **MCP4725** (12 bit, I²C) or PWM + RC low-pass substitutes
  for the internal DAC without touching the rest of the generator.
- Only the classic ESP32 and the S2 have DACs at all; the S3 and C3 do not.
- The napari widget sends `amplitude = 1` by default, which is the **full**
  swing. Start lower while aligning, or lower the `GALVO_MAX_AMPLITUDE` ceiling
  in `config.h` so a wrong host value cannot slam the mirror.

Until then `/dac_act` is acknowledged with `"return":0` and nothing moves, so
`--no-auto-galvo` is optional rather than required.

## Protocol

One compact JSON object per line, `\n`-terminated, with a `task` and a `qid`
the reply echoes back. Replies are framed by `++` and `--` marker lines:

```
> {"task":"/motor_act","qid":7,"motor":{"steppers":[{"stepperid":0,"position":1024,"speed":400,"isabs":0}]}}
< ++
< {"qid":7,"return":1}
< --
< ++
< {"qid":7,"steppers":[{"stepperid":0,"position":1024,"isDone":1}]}
< --
```

A blocking move gets one acknowledgement plus one reply per moving axis, which
is what UC2-REST counts (`Motor.move_stepper`: `nResponses = axes + 1`).

| Endpoint | Implemented |
|----------|-------------|
| `/state_get` | identity, free heap, busy flag |
| `/state_set`, `/state_act` | debug flag, `delay`, `restart` |
| `/laser_act` | `LASERid` 1–3, `LASERval` 0–255 → PWM |
| `/laser_set` | re-route a laser channel to another pin |
| `/dac_act` | accepted, answered `"return":0` — the galvo is on the UC2 board (see above) |
| `/motor_act` | move (relative/absolute), `isforever`, `isstop`, `setpos` |
| `/motor_get` | positions of A,X,Y,Z and the busy flag |
| `/motor_set` | set position; pins and limits stay compile-time |
| `/home_act` | endstop homing (axes that have an endstop) |
| `/ledarr_act` | accepted, answered `"return":0` — no LED matrix on this rig |

Axis numbering is fixed by the protocol: **A=0, X=1, Y=2, Z=3**. A is the
capillary rotation axis.

Two parser rules of the host are worth knowing before extending this firmware
(see [`src/protocol.h`](src/protocol.h)): a reply line containing the substring
`error` aborts the pending command on the host side, and a line that is exactly
`reboot` makes it drop the command. Refusals are therefore reported as
`{"return":0}`, never as prose.

## Integration checklist

- ImSwitch must **not** own an ESP32 device when the napari plugin drives the
  board directly — one process per COM port. Use the camera-only setup
  ([`LSFT_camera.json`](../acquisition/LSFT_camera.json)).
- An ImSwitch positioner only exposes the rotation axis if its `axes` list
  contains `"A"` (see [`acquisition/README.md`](../acquisition/README.md)).
- Set `steps_per_turn` / `--units-per-rev` to 4096, not the 3200 default.
- Board identity: `/state_get` reports `LSFT-ESP32`, which is what
  `ESP32Controller.identify()` prints — a quick way to confirm you are talking
  to this firmware and not to a stock UC2 board.

## Troubleshooting

| Symptom | Cause |
|---------|-------|
| Motor buzzes, does not turn | Coil pair order wrong — swap `pin[1]` and `pin[2]` of the A axis in `config.h` |
| Rotation loses steps under load | `maxSpeed` too high, or the ULN2003 is fed from 3V3 instead of 5 V |
| ESP32 resets when the motor starts | Supply brown-out; power the driver board separately, common ground |
| Host reports "Wrong Firmware" | Wrong COM port, or another process (ImSwitch) already holds it |
| Reconstruction sheared or blurred | `steps_per_turn` not calibrated — see above |
| "Light sheet ON" does nothing | Expected — the sweep is on the UC2 galvo board, not on this ESP32 |
