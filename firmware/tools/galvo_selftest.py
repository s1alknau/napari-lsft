#!/usr/bin/env python3
"""
Bench test for the UC2 galvo scanner board -- is the mirror actually moving?

The board answering JSON only proves the microcontroller is alive. It says
nothing about the MCP4822, the LM324 output stage, the cabling or the mirror.
This walks the chain from the outside in, so a failure lands in one place.

Needs only pyserial, so it runs without napari or ImSwitch:

    python galvo_selftest.py --port COM7               # everything
    python galvo_selftest.py --port COM7 --staircase   # DC steps, for a meter
    python galvo_selftest.py --port COM7 --sweep       # visible sweep only
    python galvo_selftest.py --port COM7 --park 2048   # hold one position

## What to measure

**Staircase** is the decisive test and needs no optics. The board is put in
SINGLE mode, which holds the mirrors at a fixed DAC code, and stepped through
0 / 1024 / 2048 / 3072 / 4095. Put a multimeter on the X (and then Y)
differential output and read it at each step: the voltage must move
monotonically across roughly the full +/-10 V. If it does, DAC, op-amp stage
and wiring are all good and anything still wrong is mechanical or optical.

If the voltage does not move at all, the fault is before the connector --
suspect the SPI wiring to the MCP4822 (SCK GPIO7, SDI GPIO9, CS GPIO8,
LDAC GPIO6 on the UC2 board pinout).

**Sweep** is the visual check: with the illumination on, a slow wide sweep
should draw a visible line rather than a point.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial is required:  pip install pyserial")

DAC_MAX = 4095


class Board:
    """Minimal client for the galvo firmware's ++/-- framed JSON.

    Three quirks of that firmware are handled here, all of them load-bearing:
    opening the port reboots the board, replies take up to a whole rendered
    frame, and a long command overruns the receive buffer while it is drawing
    and is then dropped without a reply. So: wait after opening, wait long for
    replies, and keep every command short.
    """

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 12.0,
                 debug: bool = False):
        self.timeout = timeout
        self.debug = debug
        self.ser = serial.Serial(port, baudrate, timeout=0.3)
        time.sleep(2.5)                       # it reboots when the port opens
        self.ser.reset_input_buffer()
        self.qid = 0

    def close(self):
        self.ser.close()

    def send(self, payload: dict):
        self.qid += 1
        raw = json.dumps(dict(payload, qid=self.qid)) + "\n"
        if self.debug:
            print("   >", raw.strip())
        self.ser.write(raw.encode())

        deadline = time.time() + self.timeout
        buffer, inside = "", False
        while time.time() < deadline:
            line = self.ser.readline().decode("utf-8", "replace").strip()
            if not line:
                continue
            if self.debug:
                print("   <", line)
            if line == "++":
                inside, buffer = True, ""
            elif line == "--":
                try:
                    return json.loads(buffer)
                except json.JSONDecodeError:
                    return {"unparsed": buffer}
            elif inside:
                buffer += line
            # everything else is ESP_LOG chatter from the render loop
        return None


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    return ok


def volts(code: int) -> str:
    """Roughly what the differential output should read at this DAC code."""
    return f"{(code / DAC_MAX * 20.0) - 10.0:+6.1f} V"


def staircase(board: Board, axis: str, dwell: float, results: list):
    print(f"\n--- {axis} staircase: put the meter on the {axis} output ---")
    key = "X_POS" if axis == "X" else "Y_POS"
    other = "Y_POS" if axis == "X" else "X_POS"
    for code in (0, 1024, 2048, 3072, DAC_MAX):
        r = board.send({"task": "/galvo_act", "SINGLE": True,
                        key: code, other: 2048})
        ok = bool(r) and r.get("status") == "success"
        print(f"   {axis} = {code:>4}   expect about {volts(code)}"
              f"   {'ok' if ok else 'NO REPLY'}", flush=True)
        results.append(ok)
        time.sleep(dwell)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="COM7")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dwell", type=float, default=4.0,
                    help="seconds to hold each staircase step (default 4)")
    ap.add_argument("--staircase", action="store_true", help="DC steps only")
    ap.add_argument("--sweep", action="store_true", help="visible sweep only")
    ap.add_argument("--park", type=int, metavar="CODE",
                    help="hold both mirrors at CODE and exit")
    ap.add_argument("--debug", action="store_true", help="dump raw traffic")
    args = ap.parse_args(argv)

    print(f"Port {args.port} @ {args.baud}  (opening resets the board)")
    board = Board(args.port, args.baud, debug=args.debug)
    results = []

    try:
        state = board.send({"task": "/state_get"}) or {}
        name = state.get("identifier_name")
        results.append(check("board identifies as a galvo scanner",
                             name == "UC2_GalvoScanner",
                             f"{name} {state.get('identifier_id', '')}"))
        if name != "UC2_GalvoScanner":
            print("\nWrong board? The LSFT laser/rotation board reports "
                  "'LSFT-ESP32' and lives on its own port.")
            return 1

        if args.park is not None:
            code = max(0, min(DAC_MAX, args.park))
            r = board.send({"task": "/galvo_act", "SINGLE": True,
                            "X_POS": code, "Y_POS": code})
            results.append(check(f"parked at {code}",
                                 bool(r) and r.get("status") == "success",
                                 f"expect about {volts(code)} on both outputs"))
            return 0 if all(results) else 1

        do_all = not (args.staircase or args.sweep)

        if args.staircase or do_all:
            staircase(board, "X", args.dwell, results)
            staircase(board, "Y", args.dwell, results)

        if args.sweep or do_all:
            print("\n--- slow wide sweep on the fast axis (Y) for 10 s ---")
            print("   with the illumination on, the beam should draw a line.")
            # Short commands, slow axis parked first: that collapses the frame
            # so the ones after it are answered quickly.
            for payload in (
                {"task": "/galvo_act", "X_MIN": 2048, "X_MAX": 2048},
                {"task": "/galvo_act", "STEP_X": 1, "STEP_Y": 8},
                {"task": "/galvo_act", "Y_MIN": 256, "Y_MAX": 3840},
                {"task": "/galvo_act", "tPixelDwelltime": 200, "SNAKE": True},
                {"task": "/galvo_act", "SINGLE": False},
            ):
                r = board.send(payload)
                results.append(bool(r) and r.get("status") == "success")
            check("sweep configured and started", all(results[-5:]))
            time.sleep(10)

            back = board.send({"task": "/galvo_get"}) or {}
            results.append(check("still sweeping afterwards",
                                 back.get("SINGLE") is False,
                                 f"Y {back.get('Y_MIN')}..{back.get('Y_MAX')} "
                                 f"step {back.get('STEP_Y')}"))
    finally:
        # Leave the mirrors somewhere defined rather than mid-scan.
        try:
            board.send({"task": "/galvo_act", "SINGLE": True,
                        "X_POS": 2048, "Y_POS": 2048})
        except Exception:
            pass
        board.close()

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    if failed:
        print("A reply timing out is not always a dead board: it only reads "
              "serial between frames. Retry with a longer --dwell first.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
