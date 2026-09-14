"""
Client for the UC2 galvo scanner board (Seeed XIAO ESP32-S3 + MCP4822).

The light sheet is swept by a *second* board on its own USB port, running
``openUC2/openUC2-LaserScanner``. It speaks the same ``++``/``--`` framed JSON
as the LSFT board, but a different galvo dialect: flat ``/galvo_act`` fields
rather than the ``/dac_act`` of :meth:`ESP32Controller.light_sheet_on`. See
``acquisition/galvo_scanner.md``.

Three properties of that firmware shape this client, and all three were found
the hard way:

* **Opening the port reboots it.** pyserial asserts DTR/RTS on open and the
  S3's USB-Serial/JTAG treats that as a reset, so the first command has to wait
  out the boot or it is lost.
* **Serial is serviced once per rendered frame.** Replies take up to a whole
  frame, and a long command overruns the USB-CDC receive buffer while the board
  is busy drawing -- it is then silently truncated and never answered. So every
  command here is short, and the configuration is split across several of them
  (``/galvo_act`` merges whatever fields are present).
* **Nothing persists.** The firmware calls ``Preferences`` without ever
  initialising NVS, so settings are lost on reboot -- which, given the point
  above, means on every connect. :meth:`configure_sheet` is therefore called
  from :meth:`connect`.

Axis roles are inverted from what the names suggest: the firmware's own comment
reads *"BEWARE: Y is pixelclock"*. **Y is the fast axis** and X indexes lines,
so the sheet is swept on Y with X parked.

One more mismatch worth knowing: the board's output stage spans +/-10 V, but the
generic 20 Kpps scanner sets take +/-5 V. Every code this client sends is
clamped to that (see :data:`SCANNER_INPUT_V`), because the extra range does not
scan wider -- it asks for deflection the mirror does not have and leaves the
servo grinding against its end stop.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

#: Full scale of the on-board 12-bit DAC.
DAC_MAX = 4095
DAC_CENTER = 2048

#: What the UC2 board's output stage spans across the full DAC range.
BOARD_FULL_SCALE_V = 10.0

#: What the scanner will accept. The generic 20 Kpps sets take +/-5 V
#: differential (200 kOhm in), so the full DAC range asks for twice the
#: deflection the mirror physically has -- which does not scan wider, it drives
#: the galvo into its end stop and leaves the servo pushing against it. Keep
#: commands inside this and the mirror stays within its range.
SCANNER_INPUT_V = 5.0


def volts_to_code(volts: float) -> int:
    """DAC code for a differential output voltage."""
    return int(round((volts / BOARD_FULL_SCALE_V + 1.0) * DAC_MAX / 2.0))


def code_to_volts(code: int) -> float:
    """Differential output voltage for a DAC code."""
    return (code / DAC_MAX * 2.0 - 1.0) * BOARD_FULL_SCALE_V


class GalvoNotAvailable(RuntimeError):
    """Raised when pyserial is missing or the board could not be reached."""


class GalvoScanner:
    """Light-sheet sweep on the UC2 galvo scanner board.

    Parameters
    ----------
    port : str
        Serial port of the galvo board (e.g. ``"COM7"``). Unlike the LSFT
        board this one has no auto-detection: it enumerates as a bare
        ``USB-Serial/JTAG`` device with no distinguishing description.
    baudrate : int
        Nominal only -- the S3's USB-Serial/JTAG rate is virtual.
    timeout : float
        Seconds to wait for a reply. Generous by default because the firmware
        only reads serial between frames.
    boot_delay : float
        Seconds to wait after opening the port, while the board reboots.
    """

    def __init__(
        self,
        port: str = "COM7",
        baudrate: int = 115200,
        timeout: float = 12.0,
        boot_delay: float = 2.5,
        max_volts: float = SCANNER_INPUT_V,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.boot_delay = boot_delay
        # Codes outside this window are never sent, whatever is asked for.
        self.max_volts = max_volts
        self._code_lo = volts_to_code(-abs(max_volts))
        self._code_hi = volts_to_code(abs(max_volts))
        self._serial = None
        self._qid = 0
        self._sheet: Dict[str, Any] = dict(
            center=DAC_CENTER, width=2048, step=8, dwell_us=10,
            park_x=DAC_CENTER, snake=True,
        )

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #
    def connect(self, configure: bool = True):
        """Open the port, wait out the reboot, and apply the sweep settings."""
        try:
            import serial  # lazy: optional dependency
        except ImportError as e:
            raise GalvoNotAvailable(
                "pyserial is not installed. Run `pip install pyserial` in the "
                "environment that runs napari."
            ) from e
        try:
            self._serial = serial.Serial(self.port, self.baudrate, timeout=0.3)
        except Exception as e:  # pragma: no cover - hardware dependent
            raise GalvoNotAvailable(f"Could not open {self.port}: {e}") from e

        time.sleep(self.boot_delay)
        self._serial.reset_input_buffer()

        state = self.command({"task": "/state_get"}) or {}
        name = state.get("identifier_name")
        if name != "UC2_GalvoScanner":
            self.close()
            raise GalvoNotAvailable(
                f"{self.port} did not identify as a UC2 galvo scanner "
                f"(got {name!r}). Is this the LSFT board?"
            )
        self._identity = f"{name} {state.get('identifier_id', '')}".strip()

        if configure:
            self.configure_sheet(**self._sheet)
        return self

    def close(self):
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def identify(self) -> str:
        if not self.is_connected:
            return "not connected"
        return f"{self.port} - {getattr(self, '_identity', 'unknown board')}"

    # ------------------------------------------------------------------ #
    # Protocol
    # ------------------------------------------------------------------ #
    def command(self, payload: Dict[str, Any],
                wait: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Send one short command and return the JSON between ``++``/``--``.

        Returns ``None`` on timeout. Keep payloads short: see the module
        docstring on why a long one is dropped without a reply.
        """
        if self._serial is None:
            raise GalvoNotAvailable("Not connected - call connect() first.")

        self._qid += 1
        message = json.dumps(dict(payload, qid=self._qid)) + "\n"
        self._serial.write(message.encode())

        deadline = time.time() + (self.timeout if wait is None else wait)
        buffer, inside = "", False
        while time.time() < deadline:
            line = self._serial.readline().decode("utf-8", "replace").strip()
            if not line:
                continue
            if line == "++":
                inside, buffer = True, ""
            elif line == "--":
                try:
                    return json.loads(buffer)
                except json.JSONDecodeError:
                    return {"unparsed": buffer}
            elif inside:
                buffer += line
            # Anything else is ESP_LOG chatter from the render loop
            # ("I (38631) SPIRenderer: Drawing frame 4 / 10"); it sits outside
            # the markers, so ignoring it here is enough.
        return None

    def _clamp(self, code) -> int:
        """Keep a DAC code inside what the scanner's input can take."""
        return max(self._code_lo, min(self._code_hi, int(code)))

    def status(self) -> Dict[str, Any]:
        """Current parameters as the board reports them (``/galvo_get``)."""
        return self.command({"task": "/galvo_get"}) or {}

    # ------------------------------------------------------------------ #
    # Light sheet
    # ------------------------------------------------------------------ #
    def configure_sheet(
        self,
        center: int = DAC_CENTER,
        width: int = 2048,
        step: int = 8,
        dwell_us: int = 10,
        park_x: int = DAC_CENTER,
        snake: bool = True,
    ) -> bool:
        """Set up the 1D sweep. Returns True if every step was acknowledged.

        ``center`` and ``width`` describe the sweep on the fast (Y) axis in DAC
        counts; ``park_x`` is where the slow axis is held. Sent as four short
        commands, the slow axis first: parking it collapses the frame to a
        single line, which makes every command after it answer quickly.
        """
        self._sheet = dict(center=center, width=width, step=step,
                           dwell_us=dwell_us, park_x=park_x, snake=snake)

        half = max(1, int(width) // 2)
        y_min = self._clamp(int(center) - half)
        y_max = self._clamp(int(center) + half)
        park_x = self._clamp(park_x)

        steps = [
            {"task": "/galvo_act", "X_MIN": park_x, "X_MAX": park_x},
            {"task": "/galvo_act", "STEP_X": 1, "STEP_Y": max(1, int(step))},
            {"task": "/galvo_act", "Y_MIN": y_min, "Y_MAX": y_max},
            {"task": "/galvo_act", "tPixelDwelltime": max(0, int(dwell_us)),
             "SNAKE": bool(snake)},
        ]
        ok = True
        for payload in steps:
            reply = self.command(payload)
            ok = ok and bool(reply) and reply.get("status") == "success"
        return ok

    def light_sheet_on(self, **_ignored) -> bool:
        """Start sweeping.

        Accepts and ignores the ``frequency`` / ``amplitude`` keywords of
        :meth:`ESP32Controller.light_sheet_on`, so this object can stand in for
        the controller during acquisition. Those describe the ESP32's own DAC
        waveform and have no equivalent here -- the sweep geometry comes from
        :meth:`configure_sheet` instead.
        """
        if not self.configure_sheet(**self._sheet):
            return False
        reply = self.command({"task": "/galvo_act", "SINGLE": False})
        return bool(reply) and reply.get("status") == "success"

    def light_sheet_off(self) -> bool:
        """Stop sweeping and park both mirrors mid-range."""
        return self.park()

    def park(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        """Hold the mirrors at a fixed position (SINGLE mode)."""
        x = self._sheet["park_x"] if x is None else x
        y = self._sheet["center"] if y is None else y
        reply = self.command({
            "task": "/galvo_act", "SINGLE": True,
            "X_POS": self._clamp(x),
            "Y_POS": self._clamp(y),
        })
        return bool(reply) and reply.get("status") == "success"
