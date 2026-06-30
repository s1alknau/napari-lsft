#!/usr/bin/env python3
"""
Add a `setLaserGalvo` @APIExport to ImSwitch's LaserController so the LSFT
acquisition (acquire_lsft.py) can activate the light-sheet galvo over HTTP at
measurement start. ImSwitch ships galvo control only as a GUI toggle
(HoliSheetController.toggleLightsheet); this exposes it on the REST API while
leaving the manual GUI control untouched.

Idempotent: re-running it does nothing if the endpoint is already present.

Usage (run with the SAME python/env that runs ImSwitch):
    python add_galvo_endpoint.py            # auto-locate the installed imswitch
    python add_galvo_endpoint.py --path C:\\path\\to\\LaserController.py
    python add_galvo_endpoint.py --revert   # remove the endpoint again
"""
import argparse
import importlib.util
import py_compile
import re
import sys
from pathlib import Path

MARKER = "def setLaserGalvo"

METHOD = '''    @APIExport()
    def setLaserGalvo(self, laserName: str = None, frequency: float = 10,
                      amplitude: float = 1, offset: float = 0, channel: int = 1,
                      clk_div: int = 0, phase: float = 0, invert: int = 1) -> None:
        """ Configure the ESP32 galvo that forms the light sheet for a laser.

        frequency=0 stops the sweep. If laserName is None or unknown, the first
        laser is used. Added for napari-lsft LSFT acquisition so the galvo
        (light sheet) can be activated programmatically at measurement start
        while staying manually controllable via the Holo widget. """
        names = self._master.lasersManager.getAllDeviceNames()
        if laserName is None or laserName not in names:
            laserName = names[0]
        manager = self._master.lasersManager[laserName]
        if not hasattr(manager, "setGalvo"):
            raise RuntimeError(
                f"Laser {laserName!r} ({type(manager).__name__}) has no galvo; "
                "expected an ESP32LEDLaserManager.")
        manager.setGalvo(channel=channel, frequency=frequency, offset=offset,
                         amplitude=amplitude, clk_div=clk_div, phase=phase,
                         invert=invert)

'''

# Insert right before this method (present in every LaserController version).
ANCHOR = re.compile(r"^    @APIExport\(\)\n    def changeScanPower\(", re.M)


def locate() -> Path:
    spec = importlib.util.find_spec("imswitch")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("Could not import 'imswitch'. Run this with ImSwitch's env, "
                 "or pass --path to LaserController.py explicitly.")
    pkg = Path(spec.submodule_search_locations[0])
    p = pkg / "imcontrol" / "controller" / "controllers" / "LaserController.py"
    if not p.exists():
        sys.exit(f"LaserController.py not found at {p}")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args(argv)

    path = args.path or locate()
    src = path.read_text(encoding="utf-8")

    if args.revert:
        if MARKER not in src:
            print("Endpoint not present; nothing to revert.")
            return
        new = re.sub(re.escape(METHOD), "", src)
        path.write_text(new, encoding="utf-8")
        py_compile.compile(str(path), doraise=True)
        print(f"Removed setLaserGalvo from {path}")
        return

    if MARKER in src:
        print(f"setLaserGalvo already present in {path} -- nothing to do.")
        return

    m = ANCHOR.search(src)
    if not m:
        sys.exit("Could not find the insertion anchor (def changeScanPower). "
                 "Insert the METHOD from this file manually before that method.")
    new = src[:m.start()] + METHOD + src[m.start():]
    backup = path.with_suffix(".py.bak")
    backup.write_text(src, encoding="utf-8")
    path.write_text(new, encoding="utf-8")
    py_compile.compile(str(path), doraise=True)
    print(f"Patched {path}\nBackup at {backup}\nRestart ImSwitch to load the "
          "new /LaserController/setLaserGalvo endpoint.")


if __name__ == "__main__":
    main()
