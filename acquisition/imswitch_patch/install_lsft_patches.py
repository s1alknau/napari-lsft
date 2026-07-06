#!/usr/bin/env python3
"""
Apply ALL napari-lsft ImSwitch patches to one ImSwitch installation in a single
step, so LSFT acquisition works across every installed ImSwitch version:

  1. setLaserGalvo  @APIExport  (galvo / light-sheet control over HTTP)
  2. ESP32RotatorManager        (native rotator for the UC2 ESP32 A-axis)

Because patches live inside the ImSwitch package, they are lost on an ImSwitch
reinstall/upgrade -- just re-run this once per environment afterwards.

Usage (run with the SAME python/env that runs that ImSwitch):
    python install_lsft_patches.py                       # auto-locate imswitch
    python install_lsft_patches.py --imswitch-root PATH  # a specific install
                                                         # (the .../imswitch dir,
                                                         #  or a source checkout)
    python install_lsft_patches.py --revert              # undo both patches

For a source checkout that is not importable, pass --imswitch-root pointing at
its inner ``imswitch`` package directory.
"""
import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GALVO_INSTALLER = HERE / "add_galvo_endpoint.py"
ROTATOR_INSTALLER = HERE / "rotator" / "add_esp32_rotator.py"


def find_imswitch_root(explicit):
    if explicit:
        root = Path(explicit)
        if root.name != "imswitch" and (root / "imswitch").is_dir():
            root = root / "imswitch"
        if not (root / "imcontrol").is_dir():
            sys.exit(f"{root} does not look like an imswitch package "
                     "(no imcontrol/ inside).")
        return root
    spec = importlib.util.find_spec("imswitch")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("Could not import 'imswitch'. Run with ImSwitch's env, or pass "
                 "--imswitch-root pointing at the imswitch package directory.")
    return Path(spec.submodule_search_locations[0])


def run(installer, path, revert):
    cmd = [sys.executable, str(installer), "--path", str(path)]
    if revert:
        cmd.append("--revert")
    print(f"\n>>> {installer.name} --path {path}" + (" --revert" if revert else ""))
    res = subprocess.run(cmd)
    return res.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--imswitch-root", default=None,
                    help="the imswitch package dir (auto-located if omitted)")
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args(argv)

    root = find_imswitch_root(args.imswitch_root)
    laser_controller = root / "imcontrol" / "controller" / "controllers" / "LaserController.py"
    rotators_dir = root / "imcontrol" / "model" / "managers" / "rotators"

    print(f"Target ImSwitch: {root}")
    rc = 0
    rc |= run(GALVO_INSTALLER, laser_controller, args.revert)
    rc |= run(ROTATOR_INSTALLER, rotators_dir, args.revert)
    print("\nDone." + ("" if args.revert else
          " Restart ImSwitch to load the new endpoint + rotator."))
    sys.exit(1 if rc else 0)


if __name__ == "__main__":
    main()
