#!/usr/bin/env python3
"""
Bench test for the LSFT firmware -- talks the UC2-REST serial protocol directly.

Deliberately depends on nothing but pyserial: it is meant to tell you whether
the *board* is good before ImSwitch, UC2-REST or napari are in the picture, so
that a failure here is unambiguously firmware or wiring.

Checks identity, the laser PWM and the rotation axis. The galvo is only tested
if the firmware was built with GALVO_ENABLED -- on this rig the sheet sweep
comes from the UC2 galvo board and its own controller.

    python lsft_selftest.py --port COM5
    python lsft_selftest.py --port COM5 --steps 512 --speed 500
    python lsft_selftest.py --port COM5 --skip-laser

Each check prints PASS/FAIL and the raw reply, and the exit status is non-zero
if anything failed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial is required:  pip install pyserial")


class Board:
    """Minimal UC2-REST serial client: one JSON line out, ++/-- block back."""

    def __init__(self, port: str, baudrate: int = 115200, debug: bool = False):
        self.debug = debug
        self.ser = serial.Serial(port, baudrate, timeout=0.2)
        # Toggling DTR/RTS resets the ESP32; wait for the banner, then drop
        # whatever the bootloader printed on the way up.
        self.ser.setDTR(False)
        self.ser.setRTS(True)
        time.sleep(0.1)
        self.ser.setRTS(False)
        time.sleep(1.5)
        self.ser.reset_input_buffer()
        self.qid = 0

    def close(self):
        self.ser.close()

    def send(self, payload: dict, n_replies: int = 1, timeout: float = 5.0):
        self.qid += 1
        payload = dict(payload, qid=self.qid)
        raw = json.dumps(payload) + "\n"
        if self.debug:
            print("   >", raw.strip())
        self.ser.write(raw.encode())

        replies, buffer, inside = [], "", False
        deadline = time.time() + timeout
        while time.time() < deadline and len(replies) < n_replies:
            line = self.ser.readline().decode("utf-8", "replace").strip()
            if not line:
                continue
            if self.debug:
                print("   <", line)
            if line == "++":
                inside, buffer = True, ""
            elif line == "--":
                inside = False
                try:
                    replies.append(json.loads(buffer))
                except json.JSONDecodeError:
                    replies.append({"unparsed": buffer})
            elif inside:
                buffer += line
        return replies


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    return ok


def autodetect() -> str | None:
    for p in list_ports.comports():
        blob = f"{p.description} {p.manufacturer or ''}"
        if any(k in blob for k in ("CH340", "CP210", "USB Serial", "UART")):
            return p.device
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port; auto-detected if omitted")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--steps", type=int, default=256,
                    help="half-steps to rotate the A axis (default 256)")
    ap.add_argument("--speed", type=int, default=400, help="steps/s")
    ap.add_argument("--laser-channel", type=int, default=1)
    ap.add_argument("--laser-value", type=int, default=40,
                    help="0-255; kept low so the test is eye-safe by default")
    ap.add_argument("--galvo-freq", type=float, default=10.0)
    ap.add_argument("--skip-laser", action="store_true")
    ap.add_argument("--skip-motor", action="store_true")
    ap.add_argument("--debug", action="store_true", help="dump the raw traffic")
    args = ap.parse_args(argv)

    port = args.port or autodetect()
    if not port:
        return check("find a serial port", False, "pass --port explicitly") or 1

    print(f"Port {port} @ {args.baud}")
    board = Board(port, args.baud, args.debug)
    results = []

    try:
        # ---- identity -----------------------------------------------------
        r = board.send({"task": "/state_get"})
        state = (r[0].get("state") if r else None) or {}
        results.append(check(
            "/state_get answers with an identity",
            bool(state.get("identifier_name")),
            f"{state.get('identifier_name')} {state.get('identifier_id')}"))

        # ---- laser --------------------------------------------------------
        if not args.skip_laser:
            print(f"  ... laser {args.laser_channel} to {args.laser_value} for 1 s")
            r = board.send({"task": "/laser_act", "LASERid": args.laser_channel,
                            "LASERval": args.laser_value})
            results.append(check("/laser_act accepted",
                                 bool(r) and r[0].get("return") == 1))
            time.sleep(1.0)
            board.send({"task": "/laser_act", "LASERid": args.laser_channel,
                        "LASERval": 0})

        # ---- rotation -----------------------------------------------------
        if not args.skip_motor:
            board.send({"task": "/motor_act",
                        "setpos": {"steppers": [{"stepperid": 0, "posval": 0}]}})

            for direction in (+1, -1):
                target = direction * args.steps
                print(f"  ... rotating A by {target} steps at {args.speed} steps/s")
                t0 = time.time()
                # One reply acknowledges the command, one reports the axis done.
                r = board.send({"task": "/motor_act", "motor": {"steppers": [
                    {"stepperid": 0, "position": target, "speed": args.speed,
                     "isabs": 0, "isaccel": 1, "accel": 2000, "isen": 1}]}},
                    n_replies=2, timeout=30 + args.steps / max(args.speed, 1))
                done = [x for x in r if "steppers" in x]
                results.append(check(
                    f"A axis reports completion ({direction:+d})",
                    bool(done), f"{time.time() - t0:.1f} s, {r}"))

            r = board.send({"task": "/motor_get", "position": True})
            pos = None
            if r and "motor" in r[0]:
                pos = {s["stepperid"]: s["position"] for s in r[0]["motor"]["steppers"]}
            results.append(check(
                "/motor_get returns to the starting position",
                pos is not None and pos.get(0) == 0, str(pos)))

        # ---- galvo --------------------------------------------------------
        # Only when this firmware was built with GALVO_ENABLED. On this rig the
        # sweep comes from the UC2 galvo board, so /state_get reports nGalvo 0
        # and the sweep is not this board's to test.
        if state.get("nGalvo"):
            print(f"  ... galvo sweep at {args.galvo_freq} Hz for 2 s (scope GPIO25)")
            r = board.send({"task": "/dac_act", "dac_channel": 1,
                            "frequency": args.galvo_freq, "amplitude": 0.5,
                            "offset": 0, "phase": 0, "invert": 0})
            results.append(check("/dac_act accepted",
                                 bool(r) and r[0].get("return") == 1))
            time.sleep(2.0)
            board.send({"task": "/dac_act", "dac_channel": 1, "frequency": 0})
        else:
            print("  [skip] galvo -- not driven by this board (nGalvo 0)")
    finally:
        board.send({"task": "/laser_act", "LASERid": args.laser_channel,
                    "LASERval": 0})
        board.close()

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
