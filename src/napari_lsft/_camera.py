"""
Pluggable camera frame sources for LSFT acquisition.

The acquisition talks only to the :class:`FrameSource` interface, so the actual
image origin is swappable:

- :class:`ImSwitchHTTPSource`  - pull frames from a running ImSwitch over its
  REST API (``/RecordingController/snapImage``), full bit depth. Best fit for
  the step-and-shoot LSFT loop (rotate -> grab).
- :class:`GxipySource`         - grab directly from a Daheng camera via the
  ``gxipy`` SDK, without ImSwitch.
- :class:`ImSwitchStreamSource` - subscribe to ImSwitch's Socket.IO frame
  stream for a live preview (experimental; see class docstring).

Use :func:`make_frame_source` to pick one by name.

Only numpy is always required; ``requests`` (HTTP), ``gxipy`` (Daheng) and
``python-socketio`` + ``msgpack`` (stream) are imported lazily.
"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from typing import Callable, Optional

import numpy as np


class FrameSource(ABC):
    """Abstract source of camera frames."""

    @abstractmethod
    def open(self) -> "FrameSource":
        """Prepare the source (connect / start acquisition). Returns self."""

    @abstractmethod
    def get_frame(self) -> np.ndarray:
        """Return the most recent frame as a 2D numpy array."""

    def close(self) -> None:
        """Release the source."""

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()


# --------------------------------------------------------------------------- #
# ImSwitch over HTTP (snap polling) - full bit depth
# --------------------------------------------------------------------------- #

class ImSwitchHTTPSource(FrameSource):
    """Pull frames from a running ImSwitch via its REST API.

    ``/RecordingController/snapImage?output=true&toList=true`` returns the frame
    as a full-bit-depth JSON array. ``full_bit_depth=False`` uses the faster but
    8-bit ``snapNumpyToFastAPI`` PNG endpoint instead.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8001,
                 detector: Optional[str] = None, full_bit_depth: bool = True,
                 secure: Optional[bool] = None, verify: bool = False,
                 timeout: float = 30.0):
        # secure=None -> auto-detect (ImSwitch defaults to SSL/https, often with
        # a self-signed cert, hence verify defaults to False).
        self.host = host
        self.port = port
        self.detector = detector
        self.full_bit_depth = full_bit_depth
        self.secure = secure
        self.verify = verify
        self.timeout = timeout
        self._requests = None
        self.base = None

    def _url(self, scheme: str) -> str:
        return f"{scheme}://{self.host}:{self.port}"

    def open(self):
        import requests  # lazy
        try:  # quiet the self-signed-cert warning
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass
        self._requests = requests
        schemes = (["https"] if self.secure else ["http"]) if self.secure is not None \
            else ["https", "http"]
        last = None
        for scheme in schemes:
            try:
                r = requests.get(self._url(scheme) + "/SettingsController/getDetectorNames",
                                 timeout=self.timeout, verify=self.verify)
                r.raise_for_status()
                self.base = self._url(scheme)
                names = r.json()
                if self.detector is None and names:
                    self.detector = names[0]
                return self
            except Exception as e:
                last = e
        raise ConnectionError(
            f"Could not reach ImSwitch at {self.host}:{self.port} "
            f"(is it running with the HTTP server?). {last}")

    def get_frame(self) -> np.ndarray:
        req = self._requests
        if self.full_bit_depth:
            r = req.get(f"{self.base}/RecordingController/snapImage",
                        params={"output": "true", "toList": "true"},
                        timeout=self.timeout, verify=self.verify)
            r.raise_for_status()
            return np.asarray(r.json())
        # 8-bit PNG path
        from PIL import Image  # lazy
        params = {"detectorName": self.detector} if self.detector else {}
        r = req.get(f"{self.base}/RecordingController/snapNumpyToFastAPI",
                    params=params, timeout=self.timeout, verify=self.verify)
        r.raise_for_status()
        return np.asarray(Image.open(io.BytesIO(r.content)))


# --------------------------------------------------------------------------- #
# Daheng camera directly (gxipy) - no ImSwitch needed
# --------------------------------------------------------------------------- #

class GxipySource(FrameSource):
    """Grab frames straight from a Daheng camera via the Galaxy ``gxipy`` SDK.

    Requires the Daheng Galaxy SDK/driver installed on the system plus
    ``pip install gxipy``. ``index`` is 1-based in gxipy's device list.
    """

    def __init__(self, index: int = 1, exposure_us: Optional[float] = None):
        self.index = index
        self.exposure_us = exposure_us
        self._mgr = None
        self._cam = None

    def open(self):
        import gxipy as gx  # lazy
        self._mgr = gx.DeviceManager()
        dev_num, _ = self._mgr.update_device_list()
        if dev_num == 0:
            raise RuntimeError("No Daheng (gxipy) camera found.")
        self._cam = self._mgr.open_device_by_index(self.index)
        if self.exposure_us is not None:
            try:
                self._cam.ExposureTime.set(self.exposure_us)
            except Exception:
                pass
        self._cam.stream_on()
        return self

    def get_frame(self) -> np.ndarray:
        raw = self._cam.data_stream[0].get_image()
        if raw is None:
            raise RuntimeError("gxipy: no image acquired (timeout).")
        arr = raw.get_numpy_array()
        if arr is None:
            raise RuntimeError("gxipy: failed to convert image to numpy.")
        return np.asarray(arr)

    def close(self):
        try:
            if self._cam is not None:
                self._cam.stream_off()
                self._cam.close_device()
        finally:
            self._cam = None
            self._mgr = None


# --------------------------------------------------------------------------- #
# ImSwitch Socket.IO live stream (experimental)
# --------------------------------------------------------------------------- #

class ImSwitchStreamSource(FrameSource):
    """Subscribe to ImSwitch's Socket.IO frame stream (live preview).

    ImSwitch emits frames as MessagePack payloads on the ``frame`` event
    (``{'metadata': {...}, 'data': <raw>}`` for binary or ``{'image': <jpeg>}``
    for JPEG). This source keeps the latest decoded frame; :meth:`get_frame`
    returns it, and :meth:`start_stream` pushes each new frame to a callback
    (e.g. to update a napari layer).

    Experimental: ImSwitch's exact per-client frame handshake/event name can
    vary by version, so verify against your ImSwitch build. For the LSFT
    step-and-shoot acquisition prefer :class:`ImSwitchHTTPSource`; use this for
    a live view only.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 8001,
                 event: str = "frame", secure: bool = False):
        scheme = "https" if secure else "http"
        self.url = f"{scheme}://{host}:{port}"
        self.event = event
        self._sio = None
        self._latest: Optional[np.ndarray] = None
        self._callback: Optional[Callable[[np.ndarray], None]] = None

    def _decode(self, payload) -> Optional[np.ndarray]:
        import msgpack  # lazy
        try:
            msg = msgpack.unpackb(payload, raw=False)
        except Exception:
            return None
        if not isinstance(msg, dict):
            return None
        if "image" in msg:  # JPEG
            from PIL import Image
            return np.asarray(Image.open(io.BytesIO(msg["image"])))
        if "data" in msg:  # raw binary
            meta = msg.get("metadata", {})
            shape = meta.get("shape")
            dtype = meta.get("dtype", "uint8")
            buf = np.frombuffer(msg["data"], dtype=dtype)
            return buf.reshape(shape) if shape else buf
        return None

    def open(self):
        import socketio  # lazy
        self._sio = socketio.Client(reconnection=True)

        @self._sio.on(self.event)
        def _on_frame(payload):  # noqa: unused
            frame = self._decode(payload)
            if frame is not None:
                self._latest = frame
                if self._callback is not None:
                    self._callback(frame)

        self._sio.connect(self.url, socketio_path="/socket.io")
        return self

    def start_stream(self, callback: Callable[[np.ndarray], None]):
        """Call ``callback(frame)`` for every incoming frame."""
        self._callback = callback

    def get_frame(self) -> np.ndarray:
        if self._latest is None:
            raise RuntimeError("No frame received yet from the stream.")
        return self._latest

    def close(self):
        try:
            if self._sio is not None:
                self._sio.disconnect()
        finally:
            self._sio = None


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

_SOURCES = {
    "imswitch-http": ImSwitchHTTPSource,
    "imswitch-stream": ImSwitchStreamSource,
    "gxipy": GxipySource,
}


def make_frame_source(kind: str = "imswitch-http", **kwargs) -> FrameSource:
    """Create a frame source by name.

    kind : "imswitch-http" (default) | "imswitch-stream" | "gxipy"
    """
    try:
        cls = _SOURCES[kind]
    except KeyError:
        raise ValueError(
            f"Unknown frame source {kind!r}. Options: {sorted(_SOURCES)}"
        )
    return cls(**kwargs)
