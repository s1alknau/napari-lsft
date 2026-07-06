#!/usr/bin/env python3
"""
Install ESP32RotatorManager into the ImSwitch installation so a setup file's
`rotators` section can use the UC2 ESP32 "A" axis as a rotator (napari-lsft
"Route B"). ImSwitch discovers rotator managers by managerName: the
RotatorsManager (a MultiManager) does
`importlib.import_module('...managers.rotators.<managerName>')` and takes the
class of the same name from that module -- so a plain file copy into
`imswitch/imcontrol/model/managers/rotators/` is all the registration needed.

Idempotent: re-running does nothing if the installed file is up to date.

Usage (run with the SAME python/env that runs ImSwitch):
    python add_esp32_rotator.py             # auto-locate the installed imswitch
    python add_esp32_rotator.py --path C:\\path\\to\\managers\\rotators
    python add_esp32_rotator.py --revert    # remove the manager again
"""
import argparse
import importlib.util
import py_compile
import shutil
import sys
from pathlib import Path

FILENAME = "ESP32RotatorManager.py"
SOURCE = Path(__file__).resolve().parent / FILENAME


def locate() -> Path:
    spec = importlib.util.find_spec("imswitch")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("Could not import 'imswitch'. Run this with ImSwitch's env, "
                 "or pass --path to the managers/rotators directory explicitly.")
    pkg = Path(spec.submodule_search_locations[0])
    d = pkg / "imcontrol" / "model" / "managers" / "rotators"
    if not d.is_dir():
        sys.exit(f"Rotators manager directory not found at {d}")
    return d


def removeCompiled(target: Path) -> None:
    """ Remove stale byte-compiled copies of the manager. """
    pycache = target.parent / "__pycache__"
    if pycache.is_dir():
        for cached in pycache.glob(f"{target.stem}.*.pyc"):
            cached.unlink()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=None,
                    help="managers/rotators directory of the imswitch install")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args(argv)

    directory = args.path or locate()
    target = directory / FILENAME

    if args.revert:
        if not target.exists():
            print(f"{FILENAME} not present in {directory} -- nothing to revert.")
            return
        target.unlink()
        removeCompiled(target)
        print(f"Removed {target}\nRestart ImSwitch; configs referencing "
              "ESP32RotatorManager will no longer load a rotator.")
        return

    if not SOURCE.exists():
        sys.exit(f"Source file not found at {SOURCE}")
    src = SOURCE.read_text(encoding="utf-8")

    if target.exists() and target.read_text(encoding="utf-8") == src:
        print(f"{FILENAME} already up to date in {directory} -- nothing to do.")
        return

    updating = target.exists()
    shutil.copyfile(SOURCE, target)
    removeCompiled(target)
    py_compile.compile(str(target), doraise=True)
    print(f"{'Updated' if updating else 'Installed'} {target}\n"
          "The manager is discovered by name -- no further registration is "
          "needed. Restart ImSwitch and use a config with a 'rotators' "
          "section pointing at ESP32RotatorManager (see LSFT_routeB.json).")


if __name__ == "__main__":
    main()
