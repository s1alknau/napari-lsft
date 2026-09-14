# Light sheet via the UC2 galvo scanner board

Plan for driving the sheet sweep with the **UC2 galvo scanner board and its
Seeed Studio controller**, instead of from the LSFT ESP32
([`firmware/`](../firmware/README.md), which does laser + rotation only).

Everything below marked ✅ was read out of the installed code; ❓ has to be
confirmed on the bench with the board in hand.

> **Decided: Path B** — the board runs `openUC2/openUC2-LaserScanner` on its own
> USB port and is driven from `napari_lsft`, not through ImSwitch's galvo
> manager. The ImSwitch route is documented below because it is what the
> *other* firmware variant fits; it is not the route being taken.

## How the software already supports it

✅ ImSwitch (`ImSwitch-basis`, the editable install behind the `imswitch21`
env) already ships the whole chain — this does **not** have to be written:

| Layer | File |
|-------|------|
| Manager | `imcontrol/model/managers/galvoscanners/ESP32GalvoScannerManager.py` |
| Base + affine calibration | `.../galvoscanners/GalvoScannerManager.py` |
| Multi-manager | `imcontrol/model/managers/GalvoScannersManager.py` |
| REST controller | `imcontrol/controller/controllers/GalvoScannerController.py` |
| Setup-file section | `imcontrol/model/SetupInfo.py` → `galvoScanners` |

The controller exports `startGalvoScan`, `stopGalvoScan`,
`setGalvoScanConfig`, `getGalvoScannerStatus`, plus arbitrary-point scanning
(`setArbitraryPoints`, `start/stop/pause/resumeArbitraryScan`) over the HTTP
API — the same API [`acquire_lsft.py`](acquire_lsft.py) already talks to.
There is a controller but **no GUI widget**, so this is REST-only.

✅ The manager reaches the board the ordinary UC2 way:

```python
self._rs232manager = lowLevelManagers['rs232sManager'][rs232device]
self._galvo = self._rs232manager._esp32.galvo
```

So the galvo board is just *another UC2-REST serial device*. It answers
`/galvo_act` and `/galvo_get`, and its DAC coordinates are **0–4095, i.e. 12
bit** — not the 8-bit internal DAC of a plain ESP32. That is consistent with a
dedicated scanner board carrying its own DAC.

## Consequence for the wiring (Path A)

Because the manager takes `rs232device` from the setup file, the galvo board
can be a **second serial device** next to the LSFT ESP32:

```
LSFT ESP32   (COM_x)  ->  laser, LED, capillary rotation   [firmware/]
UC2 galvo    (COM_y)  ->  /galvo_act, /galvo_get           [UC2 firmware]
```

Nothing in [`firmware/`](../firmware/) has to change for this — it already
answers `/dac_act` with `"return":0` so a host with auto-galvo does not stall.

```jsonc
"rs232devices": {
    "ESP32":      { "managerName": "ESP32Manager",
                    "managerProperties": { "serialport": "COM_x" } },
    "ESP32Galvo": { "managerName": "ESP32Manager",
                    "managerProperties": { "serialport": "COM_y" } }
},
"galvoScanners": {
    "LightSheetGalvo": {
        "managerName": "ESP32GalvoScannerManager",
        "managerProperties": {
            "rs232device": "ESP32Galvo",
            "nx": 256, "ny": 1,
            "x_min": 500, "x_max": 3500,
            "y_min": 2048, "y_max": 2048,
            "sample_period_us": 1,
            "frame_count": 0
        }
    }
}
```

> **Hardcode both COM ports.** The `"serialport": "auto"` that the current
> setups use makes UC2-REST scan for *any* CH340/CP2102 board and take the
> first one it finds — with two UC2 boards plugged in it will sooner or later
> attach the galvo board as the motor board. `ESP32Controller.identify()`
> already warns when the port it opened is not the one requested, and the LSFT
> firmware reports `identifier_name: "LSFT-ESP32"` in `/state_get`, so the two
> are easy to tell apart once you look.

A light sheet is a **1D** sweep, so one axis has to be parked. Which one is
not obvious — see the axis note under Path B: in the LaserScanner firmware
**Y is the fast axis** and X is the slow one, the opposite of what the names
suggest. ❓ Whether the CAN-slave variant keeps the same convention is
unverified.

## Blocker to clear first

❌ **The installed UC2-REST is too old.** `ESP32GalvoScannerManager` calls
`galvo.set_galvo_scan()`, `stop_galvo_scan()`, `get_galvo_status()` and
`set_arbitrary_points()`:

| Env | UC2-REST | Has the galvo scanner API? |
|-----|----------|----------------------------|
| `imswitch21` (**the one ImSwitch runs in**) | v0.2.0.32 | **no** — only `/dac_act` and `/scanner_act` |
| `imswitch_qt5new` | v0.2.0.32 | no |
| `nematostella_rig` | v0.2.0.40 | **yes** — `/galvo_act`, `/galvo_get` |

As it stands the manager would construct fine and then fail on the first
`start_scan` with `AttributeError: set_galvo_scan`. So step one is upgrading
UC2-REST inside `imswitch21` to ≥ v0.2.0.40 — and then re-checking that the
existing motor/laser path still behaves, since that is the same library the
rotation depends on.

## Endpoints the board firmware must answer

From UC2-REST v0.2.0.40 (`uc2rest/galvo.py`), for reference when checking what
the Seeed controller is actually flashed with:

```jsonc
// start / reconfigure a scan
{"task":"/galvo_act","config":{
    "nx":256,"ny":256,"x_min":500,"x_max":3500,"y_min":500,"y_max":3500,
    "sample_period_us":1,"frame_count":0,"bidirectional":false,
    "pre_samples":0,"fly_samples":0,"trig_delay_us":0,"trig_width_us":0,
    "line_settle_samples":0,"enable_trigger":1,"apply_x_lut":0,
    "overscan_samples":0,"laser_blanking":0,"hw_pixel_clock":0}}

// status -> {"running":..,"current_frame":..,"current_line":..}
{"task":"/galvo_get"}
```

`frame_count: 0` means *run forever*, which is what a light sheet wants: start
the sweep before the scan and stop it at the end, exactly as
`acquire_lsft.py` does today with the galvo.

## The board, identified

Plugged in as **COM7**, `VID:PID 303A:1001` — the ESP32 USB-Serial/JTAG
controller, which lives in ROM and shows up even with no firmware. `esptool
flash_id` reports:

```
Detecting chip type... ESP32-S3
Chip is ESP32-S3 (revision v0.2)
MAC: d8:3b:da:46:df:e8
```

So it is a **Seeed XIAO ESP32-S3**. The S3 has no DAC at all, which is why the
board carries its own — per the openUC2 documentation:

| | |
|---|---|
| MCU | Seeed XIAO ESP32-S3, does scan synthesis + trigger generation |
| DAC | **MCP4822**, 12 bit, SPI — hence the 0–4095 coordinates |
| Output | LM324 stage turns 0–3.3 V into **±10 V differential** XY |
| Triggers | three 50 Ω SMA outputs: pixel, line, frame |
| Bus | CAN 2.0B for UC2-CAN (galvo is CAN ID 15) |

A probe of COM7 with `/state_get`, `/galvo_get`, `/modules_get` and
`/motor_get` got **no reply**, so the board is not running UC2 firmware yet.
(Note for probing it: it is native USB-CDC, so toggling DTR/RTS makes the port
re-enumerate and the read fails — leave the control lines alone.)

## Do not write this firmware — it exists

Two upstream builds exist, and **they speak different `/galvo_act` payloads**.
That is the whole decision:

### Path A — `xiao-can-slave-galvo` (CAN slave)

From the openUC2 web flasher, <https://youseetoo.github.io/flasher.html>
(Chrome/Edge/Opera only, WebSerial). Direct link:
`flasher.html?firmware=xiao-can-slave-galvo&release=v1.9`.

This is the variant whose protocol **matches ImSwitch's
`ESP32GalvoScannerManager`** — the nested `config` object with `nx`, `ny`,
`x_min…y_max`, `sample_period_us`, `frame_count`, `bidirectional`.

The catch: it is a **CAN slave**. It expects a UC2 CAN master on the bus to
relay commands; it is not driven from its own USB port. Our LSFT ESP32 is not
a CAN master, so this path needs additional UC2 hardware.

### Path B — `openUC2/openUC2-LaserScanner` (standalone, USB serial) ← recommended

The openUC2 docs name this repo for this add-on board: PlatformIO env
**`UC2_3_Xiao`**, `pio run -t upload`, flashed over USB-CDC. It runs on its own
USB port, no CAN, which is exactly the two-serial-device layout above.

The catch: its `/galvo_act` uses **flat fields**, not ImSwitch's nested
`config`:

```jsonc
{"task":"/galvo_act",
 "X_MIN":0,"X_MAX":4095,"Y_MIN":2048,"Y_MAX":2048,
 "STEP_X":16,"STEP_Y":0,
 "tPixelDwelltime":100,       // microseconds
 "nFrames":0,                 // 0 = run forever
 "SNAKE":true,
 "ENABLE_TRIG_FRAME":1,"ENABLE_TRIG_LINE":1,"ENABLE_TRIG_PIXEL":0}
```

Framing is the same `++` / `--` UC2-REST envelope and `/state_get` works, so it
is the same family — only the galvo payload differs. `/state_get` identifies
itself as `identifier_name: "UC2_GalvoScanner"`, which is how you tell it apart
from the LSFT board (`"LSFT-ESP32"`) when both are plugged in.

#### ✅ Verified against the cloned repo

Vendored into [`firmware/galvo/`](../firmware/galvo/README.md) at upstream
commit `9582634`, with the four fixes it needs to build listed there.

**Axis roles are inverted from what the names suggest.** `SERIAL_INTERFACE.md`
says *"BEWARE: Y is pixelclock"*, and the SNAKE description confirms it: a line
runs `Y_MIN → Y_MAX`, and X indexes the lines. So **Y is the fast axis, X the
slow one**. For a light sheet: sweep Y, park X with `X_MIN == X_MAX`.

**Pin map** (`src/SPIRenderer.h`). The header `#define`s *both* `IS_XIAO` and
`IS_XIAO_UC2GALVOBOARD` and the `#ifdef` picks the latter, so the UC2 add-on
board pinout is what gets compiled — regardless of the `-DIS_XIAO=1` in
`platformio.ini`:

| Signal | GPIO | XIAO pad |
|--------|------|----------|
| SPI SCK | 7 | D9 |
| SPI SDI (MOSI) | 9 | D8 |
| DAC CS | 8 | D10 |
| DAC LDAC | 6 | D7 |
| Laser | 43 | — |
| Trigger pixel / line / frame | 2 / 3 / 4 | D1 / D2 / D3 |

The plain-`IS_XIAO` branch swaps SCK/SDI/CS to 8/7/9 — picking the wrong branch
gives a silent, non-working SPI bus rather than an error.

**Build gotcha:** `platformio.ini` hardcodes `upload_port =
/dev/cu.usbmodem1101`, a macOS path. On Windows the port has to be overridden
on the command line:

```bash
cd openUC2-LaserScanner
pio run -e UC2_3_Xiao -t upload --upload-port COM7
```

The env is `UC2_3_Xiao`, board `seeed_xiao_esp32s3`, framework `espidf` +
`arduino` — so the first build pulls the whole ESP-IDF toolchain.

#### ⚠️ Upstream `main` does not build — two local fixes needed

Checked out at commit `9582634` (Merge PR #2). Neither problem is ours, but
both have to be fixed before the board can be flashed, and both come back on a
fresh clone. They are working-tree changes, **not committed** — review them
with `git diff` in that repo.

**1. `src/SPIRenderer.cpp` — a bad merge dropped a `*/`.**
Commit `cc0dd46` ("Merge branch 'main' into copilot/…") lost the closing
delimiter of a block comment, so the comment that starts at line 623 runs to
817 and swallows the real `SPIRenderer::start()` and
`SPIRenderer::setSinglePosition()` — the two functions `main.cpp` calls. The
compiler reports it obliquely as `"/*" within comment` plus a pile of
`qualified-id in declaration` errors far below.

Its parent `1ae37d2` is clean and nothing after `cc0dd46` touched the file, so
the fix is to take that version back:

```bash
git checkout 1ae37d2 -- src/SPIRenderer.cpp
```

**2. `src/main.cpp` — `app_main()` still had pre-refactor code.**
It declared locals shadowing the global scan parameters, called the 7-argument
`SPIRenderer` constructor and `setParameters()` (both now take 15), and left a
stray unclosed `while (1) {` block. Worse than the compile errors: it declared
a **local** `SPIRenderer *renderer`, shadowing the global one at line 37 that
`processSerial()` pushes every `/galvo_act` through — so even after fixing the
signatures, parameter updates and the point-cloud commands would have gone
nowhere. Replaced with a single assignment to the global, built from the
values `loadParameters()` restores from NVS.

**3. Build with reduced parallelism.** A default `pio run` spawns enough
`cc1plus` processes to exhaust RAM (`cc1plus.exe: out of memory allocating
8392703 bytes`). Use `-j 2`.

Both source fixes are worth reporting upstream.

#### ✅ Flashed and running

`esptool` over the S3's USB-Serial/JTAG needs `--no-stub`; PlatformIO's upload
dies with *"Unable to verify flash chip connection"* otherwise, and its
`upload_speed=921600` has to come down to 115200 because the baud rate on that
interface is virtual and renegotiating it drops the link. What worked:

```bash
cd openUC2-LaserScanner/.pio/build/UC2_3_Xiao
python .../esptool.py --chip esp32s3 --port COM7 --no-stub     --before default_reset --after hard_reset write_flash -z     0x0 bootloader.bin 0x8000 partitions.bin     0xd000 ota_data_initial.bin 0x10000 firmware.bin
```

The board then answers:

```json
{"identifier_name":"UC2_GalvoScanner","identifier_id":"V1.0","identifier_author":"UC2", ...}
```

### Two things to know before writing a client

**1. It reboots when the port is opened.** pyserial asserts DTR/RTS on open and
the S3's USB-Serial/JTAG treats that as a reset. Wait ~2 s after opening before
the first command, or it lands in the bootloader and is lost. (Explicitly
toggling the lines is worse — the port re-enumerates and the read throws.)

**2. Serial is serviced once per frame, and long commands do not survive.**
`processSerial()` is called from the render loop, so the reply latency is a
whole frame — 2.6 s at the default 2048×2048/step-8 raster. Meanwhile the
USB-CDC receive buffer keeps filling, so a full ~330-character `/galvo_act`
is silently truncated and never answered.

Send **short** commands instead: `/galvo_act` merges whatever fields are
present, so the configuration can be split up. Park the slow axis first — that
collapses the frame and makes everything after it fast:

```jsonc
{"task":"/galvo_act","X_MIN":2048,"X_MAX":2048}   // park slow axis -> short frames
{"task":"/galvo_act","STEP_X":1,"STEP_Y":8}
{"task":"/galvo_act","Y_MIN":1024,"Y_MAX":3072}   // the sheet sweep
{"task":"/galvo_act","tPixelDwelltime":10,"SNAKE":true}
```

Verified by `/galvo_get` readback — every field stuck. Allow ~10 s per command
and expect ESP_LOG chatter (`I (38631) SPIRenderer: Drawing frame 4 / 10`)
interleaved between replies; it sits outside the `++`/`--` block, so a parser
that only collects between the markers ignores it.

**3. The parameters do NOT survive a reboot** — despite the firmware claiming
to persist them. Measured: set `X 2048..2048, Y 1024..3072`, read it back
correctly, close and reopen the port, and `/galvo_get` returns the defaults
`X 0..2048, Y 0..2048` again.

Cause: this is an ESP-IDF project with `app_main()`, and neither
`initArduino()` nor `nvs_flash_init()` is ever called, so the `Preferences`
writes in `saveParameters()` go to an uninitialised NVS and fail silently.
(A one-line `nvs_flash_init()` at the top of `app_main()` would fix it, and is
worth reporting upstream along with the two build fixes.)

Since a client has to reopen the port anyway — and reopening reboots the board
— **the sweep must be configured on every connect**. That is the more robust
design regardless, so nothing here depends on the persistence being fixed.



**Why B is still the better fit for LSFT:** it needs no extra hardware, and the
adapter is small. A light sheet is a 1D sweep on X with Y parked, so we drive
it with a handful of fields from our own code — perhaps 60 lines next to the
existing `ESP32Controller` — instead of buying a CAN master and going through
ImSwitch's scanner manager. `--no-auto-galvo` already keeps `acquire_lsft.py`
out of the way.

Whichever path is taken, the firmware is upstream. Writing one from scratch
would mean reimplementing the MCP4822 SPI renderer, the trigger generation and
the scan engine, and then maintaining a private protocol dialect.

## ✅ Wired into the plugin

[`napari_lsft/_galvo.py`](../src/napari_lsft/_galvo.py) is the client. It
encapsulates all three quirks above: it waits out the reboot on connect, splits
the configuration into short commands, and re-applies the sweep on every
connect rather than trusting the board to remember it.

```python
from napari_lsft._galvo import GalvoScanner

with GalvoScanner(port="COM7") as sheet:
    sheet.configure_sheet(center=2048, width=1024, step=4, dwell_us=20)
    sheet.light_sheet_on()
    ...
    sheet.light_sheet_off()      # parks both mirrors
```

`light_sheet_on()` / `light_sheet_off()` deliberately carry the same names as
the matching :class:`ESP32Controller` methods, so the scanner can be handed to
`acquire_stack(..., sheet=galvo)` in place of the controller. It accepts and
ignores the old `frequency` / `amplitude` keywords — those described the
ESP32's own DAC waveform and have no counterpart here.

In the widget: a **Galvo port** field and **Connect galvo** button sit under the
ESP32 connection, and the old *Galvo freq / amplitude* spinboxes are replaced by
the sweep geometry — centre, width, step, dwell, and where the slow axis parks,
all in DAC counts. **Light sheet ON/OFF** now drives this board; with no galvo
connected they say so instead of silently doing nothing, since the ESP32
fallback no longer exists (its DAC pins run the rotation stepper).

Verified end to end against both boards: sheet configured on connect, a 4-angle
acquisition with laser and rotation, sheet parked again afterwards.

## Is the mirror actually moving?

The board answering JSON only proves the microcontroller is alive. To test the
rest of the chain — MCP4822, LM324 output stage, cabling, mirror — use
**SINGLE mode**, which holds the mirrors at a fixed DAC code:

* **In the plugin:** *Light sheet* group → set **Park position** and press
  **Park both mirrors here**. Step through 0 / 1024 / 2048 / 3072 / 4095 with a
  meter on a differential output; the reading should walk monotonically across
  roughly ±10 V. The label shows the voltage to expect.
* **From the command line:**
  `python ../firmware/tools/galvo_selftest.py --port COM7 --staircase`
  does the same automatically, then `--sweep` runs a slow wide sweep that
  should draw a visible line rather than a point.

If the voltage does not move at all, the fault is before the connector —
suspect the SPI wiring to the DAC (SCK GPIO7, SDI GPIO9, CS GPIO8, LDAC GPIO6).
This test involves neither the scan engine nor any optics, so it splits the
problem cleanly in two.

**Only one process can hold the port.** The CLI tool and the plugin cannot both
be connected: disconnect in napari (or close it) before running the tool.

## ⚠️ Open: the mirror does not move

The board is flashed, answers every command and renders frames, but no motion
has been observed or heard at any setting. Ruled out so far, each with
evidence rather than inspection:

| Hypothesis | Status |
|---|---|
| Firmware not running | ruled out — frames drawn every 33 ms in the log |
| SPI not initialised | ruled out — `spi_bus_initialize` is under `ESP_ERROR_CHECK`; a failure reboots the board, and it runs for hours |
| Commands not arriving | ruled out — every `/galvo_act` acknowledged, `/galvo_get` reads the values back |
| SPI clock marginal | addressed — 20 MHz (the MCP4822 maximum) lowered to 2 MHz |
| Wrong SPI pin mapping | tried both branches of `SPIRenderer.h`; silent on each |
| Sweep too slow to hear | ruled out — the main loop's `vTaskDelay(10 ms)` and per-frame `ESP_LOGI` capped it near 30 Hz; both removed, tested up to 900 Hz, still silent |

**Prime suspect: the analog rails.** The schematic carries `+15V`, `-15V`,
`+VCLEAN` and `-VCLEAN`, and the openUC2 documentation describes integrated
±12 V/5 V/3.3 V rails with a 2 A budget for external driver modules. The LM324
output stage can only swing ±10 V if those rails are present, and **USB-C into
the XIAO does not produce them**. That single fact fits every observation:
microcontroller alive, firmware correct, output stage dead.

Check, in this order:

1. Is the board's own power input connected (JST-XH per the docs) to a supply,
   not just USB-C to the XIAO?
2. Measure the ±15 V rails on the board itself.
3. Does the galvo driver have its own supply?
4. Only then: with the sweep running, is there AC on the differential output?
   A ~5 V p-p triangle reads roughly 1.4 V on a meter's AC range.

If the rails turn out fine and it is still silent, flip the pin mapping — the
comment in `SPIRenderer.h` says how.

### Firmware changes made while diagnosing

Beyond the two build fixes above, and worth keeping regardless:

* SPI clock 20 MHz → 2 MHz. 16-bit transfers, so the cost is negligible.
* `vTaskDelay(pdMS_TO_TICKS(10))` → `vTaskDelay(1)` in the main loop, and the
  per-frame `ESP_LOGI` demoted to `ESP_LOGD`. Before this the *loop*, not the
  scan, set the sweep rate and capped it near 30 Hz — too slow for a light
  sheet regardless of this fault. Now the scan itself sets the rate.

## The scanner set, and two mismatches it revealed

The rig uses a generic **20 Kpps galvo set** (BeamQ). Its published spec settles
two things that had been guesswork:

| | Scanner set wants | UC2 board provides |
|---|---|---|
| Signal input | **±5 V** differential, 200 kΩ | **±10 V** differential |
| Supply | **+15 V @ 1.0 A, −15 V @ 0.6 A** | ±12 V pass-thru, 2 A max |

**1. The supply was starved.** Roughly 1.6 A across the two rails, taken through
the board's 12 V→−12 V converter, needs on the order of **2 A at the 12 V
input for one axis**. The bench supply was limited to **0.45 A**. The converter
could never start: it pulls, the input sags, it aborts, it retries seconds
later — which is exactly the periodic few-kHz chirp and the current cycling
between 0.2 A and 0.45 A that was observed, and why the amplifier stage sat at
a fixed 3.4 V instead of swinging.

**2. The command range is double what the scanner takes.** Driving the full DAC
range asks for twice the deflection the mirror has, so it runs to its end stop
and the servo pushes against it — consistent with ±12 V measured across the
coil output. `_galvo.py` now clamps every code it sends to the ±5 V window
(DAC 1024…3071); see `SCANNER_INPUT_V` there.

## What stays as it is

- [`firmware/`](../firmware/README.md) — laser + rotation, unchanged.
  `GALVO_ENABLED` stays 0; the internal-DAC generator remains available as a
  fallback if the scanner board disappoints.
- Rotation calibration (`steps_per_turn = 4096`) is unaffected.
- `acquire_lsft.py --no-auto-galvo` becomes the normal case, since
  `setLaserGalvo` targets the laser manager's `/dac_act`, not the scanner
  board. Driving the sheet through `startGalvoScan` instead is a small change
  to the script once the board answers.
