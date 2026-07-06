"""
Hardware-free test for acquire_lsft.py.

Spins up a mock ImSwitch HTTP server that implements the endpoints the
acquisition script uses, runs a full acquisition against it, and asserts the
orchestration is correct: galvo on before rotation, laser on/off, the right
number of A-axis moves, no stage translation by default, and a correctly
shaped output TIFF.

Runnable either with pytest or directly:  python test_acquire_lsft.py
"""

import importlib.util
import json
import socket
import sys
import threading
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np
import tifffile

# import the script under test from the parent directory
_ACQ = Path(__file__).resolve().parent.parent / "acquire_lsft.py"
_spec = importlib.util.spec_from_file_location("acquire_lsft", _ACQ)
acquire_lsft = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(acquire_lsft)


FRAME_H, FRAME_W = 4, 5  # tiny fake frame


class MockImSwitch(BaseHTTPRequestHandler):
    calls = []  # (route, params) in call order, shared across requests

    def log_message(self, *a):  # silence
        pass

    def _json(self, obj):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_GET(self):
        u = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(u.query).items()}
        MockImSwitch.calls.append((u.path, params))
        route = u.path

        if route == "/SettingsController/getDetectorNames":
            return self._json(["LightSheet", "Holography"])
        if route == "/LaserController/getLaserNames":
            return self._json(["635 Laser", "LED"])
        if route == "/PositionerController/getPositionerNames":
            return self._json(["ESPStage"])
        if route == "/PositionerController/getPositionerPositions":
            return self._json({"ESPStage": {"X": 0, "Y": 0, "Z": 0, "A": 0}})
        if route == "/RecordingController/snapImage":
            frame = np.full((FRAME_H, FRAME_W), 7, dtype=np.uint16)
            return self._json(frame.tolist())
        # control endpoints just acknowledge
        if route in (
            "/LaserController/setLaserValue",
            "/LaserController/setLaserActive",
            "/LaserController/setLaserGalvo",
            "/PositionerController/movePositioner",
            "/PositionerController/homeAxis",
            "/SettingsController/setDetectorExposureTime",
        ):
            return self._json(None)
        self.send_response(404)
        self.end_headers()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _serve():
    port = _free_port()
    httpd = HTTPServer(("127.0.0.1", port), MockImSwitch)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


def _routes(calls):
    return [c[0] for c in calls]


def test_full_acquisition_orchestration():
    MockImSwitch.calls = []
    httpd, port = _serve()
    try:
        out = Path(tempfile.mkdtemp()) / "stack.tif"
        n_angles = 6
        acquire_lsft.main([
            "--host", "127.0.0.1", "--port", str(port),
            "--n-angles", str(n_angles),
            "--angle-start", "0", "--angle-stop", "180",
            "--units-per-rev", "3600",  # -> 10 units per degree
            "--laser-power", "150", "--settle", "0",
            "--out", str(out),
        ])
    finally:
        httpd.shutdown()

    calls = MockImSwitch.calls
    routes = _routes(calls)

    # 1) output stack shape = (n_angles, H, W)
    stack = tifffile.imread(str(out))
    assert stack.shape == (6, FRAME_H, FRAME_W), stack.shape

    # 2) exactly one snap per angle
    assert routes.count("/RecordingController/snapImage") == 6

    # 3) A-axis moved (n_angles - 1) times (first frame at start angle)
    moves = [c for c in calls
             if c[0] == "/PositionerController/movePositioner"
             and c[1].get("axis") == "A"]
    assert len(moves) == 5, len(moves)
    # step = (180/6 deg) * (3600/360 units/deg) = 30 * 10 = 300 units
    assert abs(float(moves[0][1]["dist"]) - 300.0) < 1e-6, moves[0][1]

    # 4) galvo activated (freq>0) BEFORE the first move, stopped (freq=0) at end
    galvo = [(i, c) for i, c in enumerate(calls)
             if c[0] == "/LaserController/setLaserGalvo"]
    assert len(galvo) == 2, galvo
    first_galvo_i, first_galvo = galvo[0]
    last_galvo_i, last_galvo = galvo[-1]
    assert float(first_galvo[1]["frequency"]) > 0
    assert float(last_galvo[1]["frequency"]) == 0
    first_move_i = next(i for i, c in enumerate(calls)
                        if c[0] == "/PositionerController/movePositioner")
    assert first_galvo_i < first_move_i, "galvo must turn on before rotating"

    # 5) laser turned on then off
    active = [c for c in calls if c[0] == "/LaserController/setLaserActive"]
    assert active[0][1]["active"] == "true"
    assert active[-1][1]["active"] == "false"

    # 6) stage translation NOT called by default (optional, off)
    stage_moves = [c for c in moves if False]  # A moves are rotation, not stage
    non_a_moves = [c for c in calls
                   if c[0] == "/PositionerController/movePositioner"
                   and c[1].get("axis") != "A"]
    assert non_a_moves == [], non_a_moves
    print("OK full orchestration:", stack.shape, f"{len(moves)} A-moves,",
          "galvo on->off, laser on->off, no stage move")


def test_no_auto_galvo_leaves_galvo_untouched():
    MockImSwitch.calls = []
    httpd, port = _serve()
    try:
        out = Path(tempfile.mkdtemp()) / "stack.tif"
        acquire_lsft.main([
            "--host", "127.0.0.1", "--port", str(port),
            "--n-angles", "3", "--settle", "0",
            "--no-auto-galvo", "--out", str(out),
        ])
    finally:
        httpd.shutdown()
    routes = _routes(MockImSwitch.calls)
    assert "/LaserController/setLaserGalvo" not in routes
    print("OK --no-auto-galvo: galvo endpoint never called")


def test_optional_stage_move_when_requested():
    MockImSwitch.calls = []
    httpd, port = _serve()
    try:
        out = Path(tempfile.mkdtemp()) / "stack.tif"
        acquire_lsft.main([
            "--host", "127.0.0.1", "--port", str(port),
            "--n-angles", "2", "--settle", "0",
            "--move-stage", "--stage-axis", "X", "--stage-dist", "12.5",
            "--out", str(out),
        ])
    finally:
        httpd.shutdown()
    x_moves = [c for c in MockImSwitch.calls
               if c[0] == "/PositionerController/movePositioner"
               and c[1].get("axis") == "X"]
    assert len(x_moves) == 1 and abs(float(x_moves[0][1]["dist"]) - 12.5) < 1e-6
    print("OK --move-stage: single X move of 12.5 issued")


if __name__ == "__main__":
    test_full_acquisition_orchestration()
    test_no_auto_galvo_leaves_galvo_untouched()
    test_optional_stage_move_when_requested()
    print("\nALL TESTS PASSED")
