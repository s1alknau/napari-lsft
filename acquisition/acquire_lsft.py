#!/usr/bin/env python3
"""
acquire_lsft.py -- record a rotational light-sheet stack from a *running*
ImSwitch instance and save it in the shape napari-lsft expects.

It talks to ImSwitch's HTTP API (FastAPI, default port 8001). Because the
control happens through the running ImSwitch server, the camera, laser and
stage stay owned by ImSwitch -- no device conflicts -- and the script is
**config-agnostic**: it discovers the detector, laser and the positioner
that has a rotation axis from the server at runtime, instead of hard-coding
names from a particular setup JSON.

Acquisition sequence
--------------------
    [optional] move a translation stage axis        (off by default)
    galvo on  -> light sheet formed                  (auto; --no-auto-galvo)
    laser on (set power, then activate)
    for each angle:  rotate A-axis by d_theta  ->  grab one frame  ->  append
    laser off
    galvo off -> sheet stopped
    save (n_angles, height, width) TIFF  ->  feed to napari-lsft

Notes
-----
* The galvo (light sheet) is auto-activated at measurement start and stopped
  at the end, via the setLaserGalvo API endpoint (see imswitch_patch/). The
  sheet stays manually controllable in the ImSwitch GUI. Use --no-auto-galvo
  to leave galvo control entirely manual.
* Laser power is passed straight through as a 0-255 value; PWM generation is
  handled in firmware and is not reimplemented here.
* Frames are fetched full-bit-depth via /RecordingController/snapImage
  (JSON array). Use --preview-8bit for the faster, lossy 8-bit PNG path.

Requires: requests, numpy, tifffile  (in the env you run THIS script from;
they need not be in ImSwitch's env).
"""

import argparse
import io
import sys
import time
from typing import Optional

import numpy as np
import requests
import tifffile


# ---------------------------------------------------------------------------
# Thin HTTP client for the ImSwitch FastAPI server
# ---------------------------------------------------------------------------

class ImSwitchClient:
    """Calls ImSwitch @APIExport methods as GET /{Controller}/{method}."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8001,
                 timeout: float = 30.0):
        self.base = f"http://{host}:{port}"
        self.timeout = timeout

    def _get(self, route: str, params: Optional[dict] = None, raw: bool = False):
        # FastAPI parses query params; booleans must be lower-case strings.
        clean = {}
        for k, v in (params or {}).items():
            if v is None:
                continue
            clean[k] = "true" if v is True else "false" if v is False else v
        r = requests.get(f"{self.base}{route}", params=clean, timeout=self.timeout)
        r.raise_for_status()
        return r if raw else r.json()

    # ---- discovery ----
    def detector_names(self):
        return self._get("/SettingsController/getDetectorNames")

    def laser_names(self):
        return self._get("/LaserController/getLaserNames")

    def positioner_names(self):
        return self._get("/PositionerController/getPositionerNames")

    def positioner_positions(self):
        return self._get("/PositionerController/getPositionerPositions")

    # ---- laser ----
    def set_laser_value(self, name, value):
        self._get("/LaserController/setLaserValue",
                  {"laserName": name, "value": value})

    def set_laser_active(self, name, active):
        self._get("/LaserController/setLaserActive",
                  {"laserName": name, "active": active})

    def set_galvo(self, name, frequency, amplitude=1, offset=0):
        # frequency=0 stops the sheet sweep. Needs the napari-lsft
        # setLaserGalvo patch in the running ImSwitch.
        self._get("/LaserController/setLaserGalvo",
                  {"laserName": name, "frequency": frequency,
                   "amplitude": amplitude, "offset": offset})

    # ---- stage / rotation ----
    def move(self, positioner, axis, dist, is_absolute=False, is_blocking=True,
             speed=None):
        self._get("/PositionerController/movePositioner",
                  {"positionerName": positioner, "axis": axis, "dist": dist,
                   "isAbsolute": is_absolute, "isBlocking": is_blocking,
                   "speed": speed})

    def home_axis(self, positioner, axis, is_blocking=True):
        self._get("/PositionerController/homeAxis",
                  {"positionerName": positioner, "axis": axis,
                   "isBlocking": is_blocking})

    def set_exposure(self, detector, exposure):
        self._get("/SettingsController/setDetectorExposureTime",
                  {"detectorName": detector, "exposureTime": exposure})

    # ---- frame grab ----
    def snap_full(self, detector=None):
        """Full-bit-depth frame as a numpy array (via JSON list)."""
        data = self._get("/RecordingController/snapImage",
                         {"output": True, "toList": True})
        return np.asarray(data)

    def snap_8bit(self, detector=None):
        """Fast 8-bit grayscale frame (PNG over HTTP)."""
        from PIL import Image
        r = self._get("/RecordingController/snapNumpyToFastAPI",
                      {"detectorName": detector}, raw=True)
        return np.asarray(Image.open(io.BytesIO(r.content)))


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def pick_detector(client, requested):
    names = client.detector_names()
    if not names:
        sys.exit("No detectors found in the running ImSwitch setup.")
    if requested:
        if requested not in names:
            sys.exit(f"Detector {requested!r} not found. Available: {names}")
        return requested
    return names[0]


def pick_laser(client, requested):
    names = client.laser_names()
    if requested:
        if requested not in names:
            sys.exit(f"Laser {requested!r} not found. Available: {names}")
        return requested
    if not names:
        return None
    # Prefer a real laser (name hints) over an LED.
    for n in names:
        low = n.lower()
        if "laser" in low or "635" in low or "red" in low:
            return n
    return names[0]


def find_rotation_axis(client, requested_pos, axis_name):
    """Return (positionerName, axis) for the rotation axis."""
    positions = client.positioner_positions()  # {name: {axis: pos}}
    if requested_pos:
        axes = positions.get(requested_pos, {})
        if axis_name not in axes:
            sys.exit(f"Positioner {requested_pos!r} has no {axis_name!r} axis. "
                     f"Axes: {list(axes)}")
        return requested_pos, axis_name
    for name, axes in positions.items():
        if axis_name in axes:
            return name, axis_name
    sys.exit(
        f"No positioner exposes a {axis_name!r} axis. Your ImSwitch setup "
        f"JSON must list it, e.g.  \"axes\": [\"X\", \"Y\", \"Z\", \"A\"].\n"
        f"Discovered: { {n: list(a) for n, a in positions.items()} }"
    )


# ---------------------------------------------------------------------------
# Acquisition
# ---------------------------------------------------------------------------

def acquire(client, args):
    detector = pick_detector(client, args.detector)
    laser = pick_laser(client, args.laser)
    pos, axis = find_rotation_axis(client, args.positioner, args.rotation_axis)

    print(f"Detector : {detector}")
    print(f"Laser    : {laser if laser else '(none -- not switching illumination)'}")
    print(f"Rotation : positioner {pos!r}, axis {axis!r}")

    # Motor units to advance per angle step.
    sweep_deg = args.angle_stop - args.angle_start
    step_deg = sweep_deg / args.n_angles
    units_per_deg = args.units_per_rev / 360.0
    step_units = step_deg * units_per_deg
    print(f"Sweep    : {args.angle_start}..{args.angle_stop} deg over "
          f"{args.n_angles} steps  ({step_deg:.3f} deg/step, "
          f"{step_units:.3f} motor units/step)")

    if args.exposure is not None:
        client.set_exposure(detector, args.exposure)

    if args.home:
        print("Homing rotation axis...")
        client.home_axis(pos, axis)

    # Optional translation-stage move (off by default -- stage move is optional).
    if args.move_stage:
        print(f"Moving stage {args.stage_axis} by {args.stage_dist} (optional step)...")
        client.move(pos, args.stage_axis, args.stage_dist, is_absolute=False)

    snap = client.snap_8bit if args.preview_8bit else client.snap_full

    galvo_laser = args.galvo_laser or laser
    galvo_on = args.auto_galvo and galvo_laser is not None

    # Light sheet on: activate the galvo sweep BEFORE rotating, so the sheet
    # exists for the whole scan. (Stays manually controllable via the GUI.)
    if galvo_on:
        print(f"Activating galvo (light sheet) on {galvo_laser!r} "
              f"@ {args.galvo_freq} Hz...")
        client.set_galvo(galvo_laser, args.galvo_freq, args.galvo_amplitude)
        if args.settle > 0:
            time.sleep(args.settle)

    # Illumination on.
    if laser:
        client.set_laser_value(laser, args.laser_power)
        client.set_laser_active(laser, True)

    frames = []
    try:
        for i in range(args.n_angles):
            if i > 0:  # first frame at the start angle, then advance
                client.move(pos, axis, step_units, is_absolute=False,
                            is_blocking=True, speed=args.speed)
            if args.settle > 0:
                time.sleep(args.settle)
            frame = snap(detector)
            if args.transpose:
                frame = frame.T
            frames.append(frame)
            print(f"  [{i + 1}/{args.n_angles}] angle "
                  f"{args.angle_start + i * step_deg:7.2f} deg  "
                  f"frame {frame.shape} {frame.dtype}", flush=True)
    finally:
        if laser:
            client.set_laser_active(laser, False)
        if galvo_on:
            client.set_galvo(galvo_laser, 0)  # stop the sheet sweep

    stack = np.stack(frames, axis=0)  # (n_angles, height, width)
    print(f"Stack: {stack.shape} {stack.dtype}")

    tifffile.imwrite(args.out, stack, metadata={"axes": "QYX"})
    print(f"Saved -> {args.out}")
    print("Open in napari-lsft as input of shape (n_angles, n_x, n_y_lab). "
          "Use --transpose if X/Y_lab come out swapped.")


def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # server
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8001)
    # geometry
    p.add_argument("--n-angles", type=int, required=True,
                   help="number of rotation steps / frames")
    p.add_argument("--angle-start", type=float, default=0.0)
    p.add_argument("--angle-stop", type=float, default=180.0,
                   help="end angle in degrees (180 fully covers the disk for "
                        "the signed-radius LSFT reconstruction)")
    p.add_argument("--units-per-rev", type=float, default=3200.0,
                   help="A-axis motor units for a full 360 deg turn "
                        "(HARDWARE-SPECIFIC -- calibrate this!)")
    p.add_argument("--speed", type=float, default=None,
                   help="A-axis move speed (manager units); default = config")
    # device selection (all optional -> auto-discovered)
    p.add_argument("--detector", default=None)
    p.add_argument("--laser", default=None)
    p.add_argument("--positioner", default=None)
    p.add_argument("--rotation-axis", default="A")
    # illumination
    p.add_argument("--laser-power", type=float, default=128,
                   help="0-255; passed straight through (PWM is in firmware)")
    # galvo / light sheet
    p.add_argument("--no-auto-galvo", dest="auto_galvo", action="store_false",
                   help="do NOT auto-activate the galvo; control the sheet "
                        "manually in the ImSwitch GUI instead")
    p.add_argument("--galvo-laser", default=None,
                   help="laser whose ESP32 galvo forms the sheet "
                        "(default: the illumination laser)")
    p.add_argument("--galvo-freq", type=float, default=10,
                   help="galvo sweep frequency (Hz) used to form the sheet")
    p.add_argument("--galvo-amplitude", type=float, default=1,
                   help="galvo sweep amplitude")
    p.set_defaults(auto_galvo=True)
    # camera
    p.add_argument("--exposure", type=float, default=None)
    p.add_argument("--settle", type=float, default=0.05,
                   help="seconds to wait after each move before snapping")
    p.add_argument("--transpose", action="store_true",
                   help="swap frame axes so axis1=X (capillary), axis2=Y_lab")
    p.add_argument("--preview-8bit", action="store_true",
                   help="use the fast 8-bit PNG snap instead of full bit depth")
    p.add_argument("--home", action="store_true",
                   help="home the rotation axis before scanning")
    # optional stage translation (off by default)
    p.add_argument("--move-stage", action="store_true",
                   help="optionally translate a stage axis before the scan")
    p.add_argument("--stage-axis", default="X")
    p.add_argument("--stage-dist", type=float, default=0.0)
    # output
    p.add_argument("--out", default="lsft_stack.tif")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    client = ImSwitchClient(args.host, args.port)
    try:
        client.detector_names()  # connectivity probe
    except Exception as e:
        sys.exit(f"Cannot reach ImSwitch at {client.base} "
                 f"(is ImSwitch running with the HTTP server?). {e}")
    acquire(client, args)


if __name__ == "__main__":
    main()
