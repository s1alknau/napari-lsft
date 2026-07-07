#!/usr/bin/env python3
"""
Make ImSwitchUC2 use PyQt5 consistently instead of forcing PySide6.

ImSwitchUC2 2.1.191's imcommon/applaunch.py hardcodes
`os.environ['QT_API'] = 'pyside6'` (+ PYQTGRAPH_QT_LIB=PySide6) before any Qt
import, to push napari/vispy/pyqtgraph onto PySide6. But ~17 of ImSwitch's own
modules/widgets (imnotebook, imscripting/Qsci, LightsheetWidget, ...) hardcode
`from PyQt5 import ...`. Having both bindings loaded in one process makes vispy
crash ("Refusing to import PySide6 because PyQt5.QtCore is already imported").

Since ImSwitch's own code is PyQt5, the consistent fix is to force *pyqt5*
everywhere. This flips applaunch.py's forcing to pyqt5/PyQt5.

Prerequisite: a PyQt5-only env (PyQt5 + QScintilla installed; PySide6 may stay
installed but is then unused). This patch only edits applaunch.py.

Idempotent. Usage (run with ImSwitch's env):
    python force_pyqt5.py            # auto-locate installed imswitch
    python force_pyqt5.py --path C:\\path\\to\\applaunch.py
    python force_pyqt5.py --revert   # restore pyside6 forcing
"""
import argparse
import importlib.util
import py_compile
import sys
from pathlib import Path

FWD = [("os.environ['QT_API'] = 'pyside6'", "os.environ['QT_API'] = 'pyqt5'"),
       ("os.environ['PYQTGRAPH_QT_LIB'] = 'PySide6'",
        "os.environ['PYQTGRAPH_QT_LIB'] = 'PyQt5'")]


def locate() -> Path:
    spec = importlib.util.find_spec("imswitch")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("Could not import 'imswitch'. Run with ImSwitch's env, or pass "
                 "--path to imcommon/applaunch.py.")
    p = Path(spec.submodule_search_locations[0]) / "imcommon" / "applaunch.py"
    if not p.exists():
        sys.exit(f"applaunch.py not found at {p}")
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args(argv)

    path = args.path or locate()
    src = path.read_text(encoding="utf-8")
    pairs = [(b, a) for a, b in FWD] if args.revert else FWD

    already = all(new in src for _, new in pairs)
    if already:
        print(f"{path.name}: already {'reverted' if args.revert else 'forcing pyqt5'} "
              "-- nothing to do.")
        return

    new_src = src
    for old, new in pairs:
        new_src = new_src.replace(old, new)
    if new_src == src:
        sys.exit("Neither the pyside6 nor pyqt5 markers were found; applaunch.py "
                 "may be a different version -- edit QT_API/PYQTGRAPH_QT_LIB by hand.")
    path.with_suffix(".py.bak").write_text(src, encoding="utf-8")
    path.write_text(new_src, encoding="utf-8")
    py_compile.compile(str(path), doraise=True)
    print(f"Patched {path} -> Qt binding forced to "
          f"{'pyside6 (reverted)' if args.revert else 'pyqt5'}\n"
          "Ensure PyQt5 + QScintilla are installed. Restart ImSwitch.")


if __name__ == "__main__":
    main()
