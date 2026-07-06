# ESP32RotatorManager (Route B)

An ImSwitch *rotator* manager that drives the **A axis of a UC2 ESP32 board**
so the tomography rotation stage can be used by ImSwitch's rotator-based
widgets (Rotator, and — on OPT-capable ImSwitch versions — the OPT Scan /
OPT Alignment widgets) instead of only being reachable as a positioner axis.

## What it is

- `ESP32RotatorManager.py` — the manager. It subclasses ImSwitch's abstract
  `RotatorManager` and reuses the exact same ESP32 connection mechanism as
  `ESP32StageManager`: the `rs232sManager` low-level manager, keyed by the
  `rs232device` manager property, exposing the UC2-REST `motor` object. Moves
  go over `motor.move_a(...)` (blocking, in a background thread), positions
  are tracked in software, and `sigRotatorPositionUpdated` is emitted after
  every finished move — which is what the OPT scan workflow uses to trigger
  the next projection.
- `add_esp32_rotator.py` — idempotent installer/uninstaller.

ImSwitch discovers rotator managers **by managerName**: `RotatorsManager`
(a `MultiManager`) does
`importlib.import_module('imswitch.imcontrol.model.managers.rotators.<managerName>')`
and instantiates the class of the same name. So installing is just copying
the file into that package — no `__init__.py` edits or registries.

## Install

Run with the SAME python environment that runs ImSwitch (here: `imswitch21`):

```
C:\Users\AdminAlex\miniconda3\envs\imswitch21\python.exe add_esp32_rotator.py
```

Re-running is a no-op if already installed/up to date. To remove:

```
C:\Users\AdminAlex\miniconda3\envs\imswitch21\python.exe add_esp32_rotator.py --revert
```

Then restart ImSwitch with a config that has a `rotators` section (see
`acquisition/LSFT_routeB.json`):

```json
"rotators": {
    "ESP32Rotator": {
        "managerName": "ESP32RotatorManager",
        "managerProperties": {
            "rs232device": "ESP32",
            "stepsPerTurn": 3200,
            "startSpeed": 15000
        }
    }
}
```

`stepsPerTurn` must match the physical drive: full steps per revolution ×
microstepping × any gear ratio (3200 = 200 × 16, no gearing). In
`LSFT_routeB.json` the `A` axis was removed from the `ESP32StageManager`
positioner so the rotator has exclusive control of that motor.

## Using it

- **Rotator widget** (available in the installed imswitch21): add
  `"Rotator"` to `availableWidgets`. Gives manual absolute/relative moves,
  zeroing, speed and continuous rotation.
- **OPT Scan / OPT Alignment widgets**: these controllers
  (`OptController.py`, `AlignOptController.py`) do **not exist in the
  currently installed imswitchuc2 2.1.191** — they ship with the upstream
  ImSwitch OPT version (see the git checkout). On such a version, add
  `"Opt"` and `"AlignOpt"` to `availableWidgets`, then pick `ESP32Rotator`
  in the OPT widget's Rotator dropdown; the widget reads the manager's
  `_stepsPerTurn` and calls `move_abs(steps, inSteps=True)`, both of which
  this manager provides.
- The `Recording` widget is unaffected; OPT scans save their own tiff stack
  per angle plus metadata.

## Manager properties

| property | default | meaning |
|---|---|---|
| `rs232device` | (required) | name of the `ESP32Manager` entry in `rs232devices` |
| `stepsPerTurn` | 3200 | motor steps per full revolution |
| `startSpeed` | 15000 | speed in steps/s (also settable from the Rotator widget) |
| `acceleration` | null | steps/s², null = firmware default |
| `stepSizeA` | 1 | UC2-REST physical step factor; keep 1 (raw steps) |
| `backlashA` | 0 | backlash compensation in steps |
| `moveTimeout` | 30 | seconds to wait for one blocking move |
| `asyncMove` | true | run moves in a background thread and emit the position-updated signal on completion; required for OPT scans |

## Still needs hardware validation

- That `motor.move_a(..., is_blocking=True)` really returns only after the
  motion finished on this firmware (otherwise frames are captured mid-move;
  if so, add a settle delay or poll `motor.get_position()`).
- Direction/sign of rotation and the correct `stepsPerTurn` for the actual
  rotation mount (gear ratio!).
- Boot-up position readout (`get_position()[0]`) and `set_zero_pos()`
  behavior on the device.
- Continuous rotation start/stop (`move_forever` / `stop`) which the Rotator
  widget buttons use.
- No-conflict operation with the galvo/laser traffic on the same serial
  connection during an OPT scan.
