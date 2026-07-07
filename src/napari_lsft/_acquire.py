"""
LSFT acquisition orchestration for the "control in the plugin" architecture.

The plugin drives the ESP32 itself (laser / LED / galvo / rotation, via
:class:`napari_lsft._hardware.ESP32Controller`) and pulls camera frames from a
swappable :class:`napari_lsft._camera.FrameSource` (ImSwitch over HTTP, a
Socket.IO stream, or a Daheng camera directly).

Sequence (step-and-shoot, one sharp frame per rotation angle):

    [optional] translate the stage        (off by default)
    galvo on   -> light sheet formed       (auto; can be disabled)
    laser on
    for each angle:  rotate by d_theta  ->  settle  ->  grab one frame
    laser off
    galvo off

Returns a ``(n_angles, height, width)`` stack ready for napari-lsft
reconstruction.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

import numpy as np


def acquire_stack(
    controller,
    source,
    n_angles: int,
    angle_start: float = 0.0,
    angle_stop: float = 180.0,
    laser_value: int = 128,
    laser_channel: int = 1,
    auto_galvo: bool = True,
    galvo_freq: float = 10.0,
    galvo_amplitude: float = 1.0,
    settle: float = 0.05,
    rotate_speed: int = 15000,
    move_stage: Optional[dict] = None,
    transpose: bool = False,
    output: Optional[str] = None,
    return_stack: bool = True,
    progress_callback: Optional[Callable[[int, int], None]] = None,
):
    """Record a rotational light-sheet stack.

    Parameters
    ----------
    controller : ESP32Controller
        Connected controller for laser / galvo / rotation.
    source : FrameSource
        Opened frame source for the camera.
    n_angles : int
        Number of rotation steps / frames.
    angle_start, angle_stop : float
        Rotation range in degrees (180 fully covers the disk for the
        signed-radius reconstruction).
    laser_value : int
        Laser power 0-255 (PWM handled in firmware).
    auto_galvo : bool
        Activate the galvo light sheet before rotating and stop it after.
    galvo_freq, galvo_amplitude : float
        Galvo sweep parameters used to form the sheet.
    settle : float
        Seconds to wait after each move before grabbing a frame.
    move_stage : dict, optional
        Optional pre-scan stage translation, e.g. ``{"x": 100}`` (off by
        default). Only applied if given.
    transpose : bool
        Swap frame axes so axis1 = X (capillary), axis2 = Y_lab.
    output : str, optional
        If given, raw frames are written incrementally to this HDF5/Zarr path
        (by extension), one angle at a time - crash-safe and RAM-friendly for
        long scans.
    return_stack : bool
        If True (default), also return the full stack in memory. Set False for
        very large scans that are streamed to ``output`` instead.
    progress_callback : callable, optional
        Called with ``(current, total)`` after each frame.

    Returns
    -------
    np.ndarray or str or None
        The ``(n_angles, height, width)`` stack if ``return_stack`` else the
        ``output`` path (or None if neither).
    """
    sweep = angle_stop - angle_start
    step_deg = sweep / n_angles

    writer = None
    if output is not None:
        from ._writer import IncrementalStackWriter
        writer = IncrementalStackWriter(output, n_frames=n_angles)

    # Optional stage translation (off by default).
    if move_stage:
        controller.move_xyz(
            x=move_stage.get("x", 0),
            y=move_stage.get("y", 0),
            z=move_stage.get("z", 0),
            is_absolute=move_stage.get("is_absolute", False),
        )

    # Light sheet on before rotating so it exists for the whole scan.
    if auto_galvo:
        controller.light_sheet_on(frequency=galvo_freq, amplitude=galvo_amplitude)
        if settle > 0:
            time.sleep(settle)

    controller.laser_on(value=laser_value, channel=laser_channel)

    frames = [] if return_stack else None
    try:
        for i in range(n_angles):
            if i > 0:  # first frame at the start angle, then advance
                controller.rotate(step_deg, speed=rotate_speed, blocking=True)
            if settle > 0:
                time.sleep(settle)
            frame = np.asarray(source.get_frame())
            if transpose:
                frame = frame.T
            if writer is not None:
                writer.append(frame)
            if frames is not None:
                frames.append(frame)
            if progress_callback:
                progress_callback(i + 1, n_angles)
    finally:
        controller.laser_off(channel=laser_channel)
        if auto_galvo:
            controller.light_sheet_off()
        if writer is not None:
            writer.close()

    if frames is not None:
        return np.stack(frames, axis=0)
    return output
