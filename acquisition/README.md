# LSFT acquisition (ImSwitch + UC2 ESP32)

Record a rotational light-sheet stack with ImSwitch and hand it to
[napari-lsft](../README.md) for reconstruction. This is **Route A**: a small
client that drives a *running* ImSwitch instance over its HTTP API. The
camera, laser and stage stay owned by ImSwitch, so there are no device
conflicts.

```
ESP32 + UC2 firmware  ->  galvo light sheet + 635 laser + capillary rotation (A-axis)
ImSwitch (HTTP API)   ->  acquire_lsft.py: rotate -> snap -> stack -> save TIFF
napari-lsft           ->  reconstruct 3D volume
```

## 1. ImSwitch setup JSON

ImSwitch always boots from a setup JSON, and it is hardware-specific. Use
[`LSFT.json`](LSFT.json) as a starting point — copy it
into your `ImSwitchConfig/imcontrol_setups/` folder and edit the few fields
that differ per rig:

| Field | What to set |
|-------|-------------|
| `rs232devices.ESP32.serialport` | left as `"auto"` — the ESP32 COM port is auto-detected (UC2-REST scans for the CH340/CP2102/USB-serial bridge). Only hardcode a port (e.g. `"COM3"`) if auto-detect picks the wrong device. |
| `detectors.*.cameraListIndex` | index of your camera (Daheng MER2-1220 via `GXPIPYManager`; `0` for a single camera). Not a COM port — USB3 cameras are addressed by list index. |
| `positioners.ESPStage.managerProperties.stepsizeA` | rotation step calibration |

**The one non-negotiable bit:** the positioner must list the rotation axis:

```json
"axes": ["X", "Y", "Z", "A"]
```

The stock UC2 light-sheet configs only list `X, Y, Z`, so the capillary cannot
be rotated until you add `"A"`. The `ESP32StageManager` already supports the
A-axis; it just has to be declared.

The galvo / light sheet is **not** a separate device — it is driven by the
ESP32 firmware through the `ESP32LEDLaserManager`. It stays manually
controllable in the ImSwitch GUI (Holo widget), and the acquisition also
activates it automatically at measurement start (see step 2b).

## 2. Run ImSwitch with the HTTP server

Start ImSwitch with that setup. The script talks to the FastAPI server
(default port **8001**). Confirm it is up by opening
`http://127.0.0.1:8001/SettingsController/getDetectorNames` in a browser.

### 2b. ImSwitch patches (one-time per install)

LSFT needs two things ImSwitch doesn't ship with: a galvo/light-sheet endpoint
(`setLaserGalvo`) and a native rotator for the ESP32 A-axis
(`ESP32RotatorManager`, Route B). Apply **both** to an ImSwitch install with one
command (run it with that ImSwitch's env), then restart ImSwitch:

```bash
python imswitch_patch/install_lsft_patches.py
```

Run it **once per installed ImSwitch version** — for a source checkout that
isn't importable, point at it explicitly:

```bash
python imswitch_patch/install_lsft_patches.py --imswitch-root C:\path\to\ImSwitch\imswitch
```

Patches live inside the ImSwitch package, so an ImSwitch reinstall/upgrade drops
them — just re-run this afterwards. `--revert` undoes both. The individual
installers ([`add_galvo_endpoint.py`](imswitch_patch/add_galvo_endpoint.py),
[`rotator/add_esp32_rotator.py`](imswitch_patch/rotator/add_esp32_rotator.py))
still exist if you want to apply just one. Manual galvo control in the GUI is
unaffected; use `--no-auto-galvo` to toggle the sheet yourself.

## 3. Record a stack

```bash
python acquire_lsft.py --n-angles 360 --angle-start 0 --angle-stop 180 \
    --units-per-rev 3200 --laser-power 150 --out nema_stack.tif
```

The script auto-discovers the detector, the laser, and the positioner that has
the `A` axis — you only override names if discovery picks the wrong one.

Key options:

| Option | Meaning |
|--------|---------|
| `--n-angles` | number of rotation steps / frames (**required**) |
| `--angle-start/--angle-stop` | rotation range in degrees (180 fully covers the disk) |
| `--units-per-rev` | **calibrate this** — A-axis motor units per 360° turn |
| `--laser-power` | 0–255, passed straight through (PWM is in firmware) |
| `--move-stage` | optionally translate a stage axis first (**off by default**) |
| `--transpose` | swap frame axes if X / Y_lab come out swapped |
| `--preview-8bit` | fast 8-bit PNG frames instead of full bit depth |
| `--home` | home the rotation axis before scanning |

Output is a `(n_angles, height, width)` TIFF — exactly the
`(n_angles, n_x, n_y_lab)` input napari-lsft expects.

## 4. Reconstruct

Open the TIFF in napari, run **Plugins → LSFT Reconstruction**, set the same
angle range, and reconstruct.

## Notes / gotchas

- **Bit depth:** full-bit-depth frames come via `/RecordingController/snapImage`
  (JSON array). `--preview-8bit` uses `snapNumpyToFastAPI`, which is faster but
  8-bit only — use it for alignment, not final data.
- **Frame orientation:** napari-lsft expects axis 1 = X (along the capillary),
  axis 2 = Y_lab (detector). If your camera is rotated, add `--transpose`.
- **Calibration first:** record a small `--n-angles 4` test, check the rotation
  range and center, then do the full scan.
- **Stage move is optional** by design: the required loop is rotate → snap.
  Translation only happens if you pass `--move-stage`.

## Later: Route B (native Opt widget)

This script intentionally bypasses ImSwitch's Opt/Rotator GUI because there is
no ESP32 *rotator* manager (only Standa / Telemetrix). Once the geometry is
validated, a small `ESP32RotatorManager` would let the Opt/AlignOpt/Recording
widgets drive the ESP32 A-axis natively. See the project notes.
