"""
Hardware-free LSFT rig for testing the acquisition and reconstruction path.

:class:`SimulatedRig` stands in for *both* halves of an acquisition: it
implements the :class:`~napari_lsft._hardware.ESP32Controller` methods used by
:func:`~napari_lsft._acquire.acquire_stack` and the
:class:`~napari_lsft._camera.FrameSource` interface. It needs no camera, no
ESP32 and no running ImSwitch::

    from napari_lsft._acquire import acquire_stack
    from napari_lsft._simulate import SimulatedRig

    rig = SimulatedRig()
    stack = acquire_stack(rig, rig, n_angles=90, auto_galvo=False)

Because it renders real parallel projections of a 3D phantom, the stack it
produces reconstructs back into that phantom -- so a round trip actually
exercises the geometry (rotation direction, centring, axis order), not just the
plumbing.
"""

from __future__ import annotations

import numpy as np

from ._camera import FrameSource


def make_phantom(n_x: int = 48, size: int = 96, seed: int = 0) -> np.ndarray:
    """Build a ``(n_x, size, size)`` phantom of blobs in a cylinder.

    The cylinder stands in for the capillary; the blobs are off-centre and of
    differing size so that a mirrored or mis-centred reconstruction is visible
    rather than plausible.
    """
    rng = np.random.default_rng(seed)
    x = np.arange(n_x)[:, None, None]
    y = np.arange(size)[None, :, None]
    z = np.arange(size)[None, None, :]
    c = (size - 1) / 2.0

    vol = np.zeros((n_x, size, size), dtype=np.float32)

    # Faint capillary: a cylinder along X, so every slice looks alike.
    r2 = (y - c) ** 2 + (z - c) ** 2
    vol += 0.15 * (r2 < (0.42 * size) ** 2)

    # Bright asymmetric blobs, deliberately off-centre.
    for _ in range(6):
        bx = rng.uniform(0.15, 0.85) * n_x
        by = c + rng.uniform(-0.25, 0.25) * size
        bz = c + rng.uniform(-0.25, 0.25) * size
        rx = rng.uniform(0.04, 0.10) * n_x
        rr = rng.uniform(0.04, 0.09) * size
        d2 = ((x - bx) / rx) ** 2 + ((y - by) / rr) ** 2 + ((z - bz) / rr) ** 2
        vol += rng.uniform(0.6, 1.0) * np.exp(-d2)

    return vol


class SimulatedRig(FrameSource):
    """A virtual capillary on a rotation stage, imaged by a virtual camera.

    Parameters
    ----------
    n_x, size : int
        Phantom shape; frames come out as ``(n_x, size)``, i.e. the
        ``(n_x, n_y_lab)`` layout the reconstruction expects.
    noise : float
        Gaussian read noise as a fraction of the peak signal (0 = off).
    dtype : numpy dtype
        Frame dtype; the default uint16 matches a real camera.
    """

    def __init__(self, n_x: int = 48, size: int = 96, noise: float = 0.01,
                 seed: int = 0, dtype=np.uint16):
        self._volume = make_phantom(n_x, size, seed)
        # Fixed full-scale reference: the brightest projection at full laser
        # power. Every frame is digitised against this one constant, so
        # projections stay on a common intensity scale across angles -- which
        # the reconstruction depends on. Normalising per frame would flatten
        # the angular contrast and stretch pure noise to full scale.
        ref = float(self._volume.sum(axis=2).max())
        self._ref = ref if ref > 0 else 1.0
        self._angle = 0.0
        self._laser = 0
        self._sheet = False
        self._noise = float(noise)
        self._dtype = np.dtype(dtype)
        self._rng = np.random.default_rng(seed + 1)
        self.position = {"X": 0.0, "Y": 0.0, "Z": 0.0}

    # ---- FrameSource ----
    def open(self) -> "SimulatedRig":
        return self

    def get_frame(self) -> np.ndarray:
        """Parallel projection of the phantom at the current rotation angle."""
        from scipy.ndimage import rotate

        # Rotate the sample in the (Y, Z) plane, then integrate along Z: the
        # light-sheet camera sees X vs Y_lab, summing what the ray passes.
        rotated = rotate(self._volume, self._angle, axes=(1, 2), reshape=False,
                         order=1, mode="constant", cval=0.0)
        frame = rotated.sum(axis=2)

        # Illumination: no laser, no signal. Keeps the control path honest.
        frame = frame * (self._laser / 255.0)
        if self._noise > 0:
            frame = frame + self._rng.normal(
                0.0, self._noise * self._ref, frame.shape
            )

        if self._dtype.kind in "ui":
            info = np.iinfo(self._dtype)
            frame = frame / self._ref * (info.max * 0.9)
            frame = np.clip(frame, 0, info.max)
        return frame.astype(self._dtype)

    def close(self) -> None:
        self._laser = 0
        self._sheet = False

    # ---- ESP32Controller stand-in ----
    def connect(self):
        return self

    @property
    def is_connected(self) -> bool:
        return True

    def set_laser(self, value: int, channel: int = 1):
        self._laser = int(value)

    def laser_on(self, value: int = 128, channel: int = 1):
        self._laser = int(value)

    def laser_off(self, channel: int = 1):
        self._laser = 0

    def light_sheet_on(self, frequency: float = 10, amplitude: float = 1,
                       offset: float = 0, channel: int = 1, value=None):
        self._sheet = True

    def light_sheet_off(self, channel: int = 1):
        self._sheet = False

    def rotate(self, degrees: float, speed: int = 15000,
               blocking: bool = True) -> float:
        self._angle += float(degrees)
        return self._angle

    @property
    def rotation_position(self) -> float:
        return self._angle

    def zero_rotation(self):
        self._angle = 0.0

    def move_xyz(self, x=0, y=0, z=0, speed=(10000, 10000, 10000),
                 is_absolute=False, blocking=True):
        for axis, d in (("X", x), ("Y", y), ("Z", z)):
            self.position[axis] = float(d) if is_absolute else self.position[axis] + float(d)

    def stop(self, axis=None):
        pass
