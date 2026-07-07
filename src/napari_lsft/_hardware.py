"""
Direct ESP32 (UC2) hardware control for LSFT, via the UC2-REST library.

This lets the napari-lsft plugin drive the whole UC2 ESP32 board itself -
laser, LED, light-sheet galvo and the motors (X/Y/Z + the A rotation axis) -
instead of routing control through ImSwitch. ImSwitch is then only needed for
the camera stream (see :mod:`napari_lsft._camera`).

The ESP32 speaks over a single serial (COM) port, which only one process may
open. So if this controller owns the ESP32, the ImSwitch setup must NOT define
an ESP32 device (camera-only config).

``uc2rest`` is an optional dependency; it is imported lazily so the rest of the
plugin (readers/writers/reconstruction) works without it. Install with
``pip install UC2-REST``.
"""

from __future__ import annotations

from typing import Optional, Tuple


class ESP32NotAvailable(RuntimeError):
    """Raised when uc2rest is missing or the ESP32 could not be reached."""


class ESP32Controller:
    """Thin, LSFT-focused wrapper around ``uc2rest.UC2Client``.

    Parameters
    ----------
    serialport : str
        COM port of the ESP32, or ``"auto"`` (default) to let UC2-REST scan
        for the CH340/CP2102/USB-serial bridge.
    baudrate : int
        Serial baud rate (default 115200).
    host : str, optional
        If set, connect over WiFi/HTTP to this host instead of serial.
    steps_per_turn : int
        Motor (micro)steps per full 360 deg revolution of the A axis; the
        conversion factor between degrees and steps for rotation.
    rotation_axis : str
        Which motor axis rotates the capillary (default ``"A"``).
    """

    def __init__(
        self,
        serialport: str = "auto",
        baudrate: int = 115200,
        host: Optional[str] = None,
        steps_per_turn: int = 3200,
        rotation_axis: str = "A",
    ):
        self.serialport = serialport
        self.baudrate = baudrate
        self.host = host
        self.steps_per_turn = steps_per_turn
        self.rotation_axis = rotation_axis
        self._client = None

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #
    def connect(self):
        """Open the connection. Returns self. Raises ESP32NotAvailable."""
        try:
            import uc2rest  # lazy: optional dependency
        except ImportError as e:
            raise ESP32NotAvailable(
                "uc2rest is not installed. Run `pip install UC2-REST` in the "
                "environment that runs napari to control the ESP32."
            ) from e
        try:
            self._client = uc2rest.UC2Client(
                host=self.host,
                serialport=None if self.host else self.serialport,
                baudrate=self.baudrate,
            )
        except Exception as e:  # pragma: no cover - hardware dependent
            raise ESP32NotAvailable(f"Could not open ESP32: {e}") from e
        return self

    @property
    def client(self):
        if self._client is None:
            raise ESP32NotAvailable("Not connected - call connect() first.")
        return self._client

    @property
    def is_connected(self) -> bool:
        return bool(self._client is not None and getattr(self._client, "is_connected", False))

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ #
    # Illumination: laser
    # ------------------------------------------------------------------ #
    def set_laser(self, value: int, channel: int = 1):
        """Set laser power (0-255). PWM generation is handled in firmware."""
        self.client.laser.set_laser(channel=channel, value=int(value))

    def laser_on(self, value: int = 128, channel: int = 1):
        self.set_laser(value=value, channel=channel)

    def laser_off(self, channel: int = 1):
        self.set_laser(value=0, channel=channel)

    # ------------------------------------------------------------------ #
    # Illumination: LED
    # ------------------------------------------------------------------ #
    def set_led(self, intensity: Tuple[int, int, int] = (255, 255, 255), on: bool = True):
        """Switch the Neopixel LED matrix/ring on/off at an RGB intensity."""
        self.client.led.setAll(state=1 if on else 0, intensity=intensity)

    def led_off(self):
        self.set_led(on=False)

    # ------------------------------------------------------------------ #
    # Light sheet: galvo
    # ------------------------------------------------------------------ #
    def light_sheet_on(
        self,
        frequency: float = 10,
        amplitude: float = 1,
        offset: float = 0,
        channel: int = 1,
        clk_div: int = 0,
        phase: float = 0,
        invert: int = 1,
    ):
        """Start the galvo sweep that forms the light sheet.

        Mirrors what ImSwitch's HoliSheet/ESP32LEDLaserManager do
        (``galvo.set_dac`` with frequency>0).
        """
        self.client.galvo.set_dac(
            channel=channel, frequency=frequency, offset=offset,
            amplitude=amplitude, clk_div=clk_div, phase=phase, invert=invert,
        )

    def light_sheet_off(self, channel: int = 1):
        """Stop the galvo sweep (frequency = 0)."""
        self.client.galvo.set_dac(channel=channel, frequency=0)

    # ------------------------------------------------------------------ #
    # Motors: rotation (A axis) + translation
    # ------------------------------------------------------------------ #
    def _deg_to_steps(self, degrees: float) -> int:
        return int(round(degrees / 360.0 * self.steps_per_turn))

    def _steps_to_deg(self, steps: float) -> float:
        return (steps % self.steps_per_turn) / self.steps_per_turn * 360.0

    def rotate(
        self,
        degrees: float,
        speed: int = 15000,
        is_absolute: bool = False,
        blocking: bool = True,
        acceleration=None,
        timeout: float = 30,
    ):
        """Rotate the capillary (A axis) by/to ``degrees``."""
        steps = self._deg_to_steps(degrees)
        self.client.motor.move_a(
            steps, speed, acceleration=acceleration,
            is_absolute=is_absolute, is_blocking=blocking, timeout=timeout,
        )

    def rotation_position(self) -> float:
        """Current A-axis position in degrees, from the device (order A,X,Y,Z)."""
        try:
            steps = self.client.motor.get_position()[0]
        except Exception:
            return 0.0
        return self._steps_to_deg(steps)

    def zero_rotation(self):
        """Define the current A position as zero on the device."""
        self.client.motor.set_position(axis=self.rotation_axis, position=0)

    def move_xyz(self, x=0, y=0, z=0, speed=(10000, 10000, 10000),
                 is_absolute=False, blocking=True):
        """Optional translation stage move (X/Y/Z)."""
        self.client.motor.move_xyz(
            value=(x, y, z), speed=speed,
            is_absolute=is_absolute, is_blocking=blocking,
        )

    def stop(self, axis: Optional[str] = None):
        self.client.motor.stop(axis=axis)
