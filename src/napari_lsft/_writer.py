"""
Writer module for exporting reconstructed volumes.

Also provides :class:`IncrementalStackWriter` for streaming raw acquisition
frames straight to disk (HDF5 or Zarr), one angle at a time, so long scans are
never held entirely in RAM and survive a crash mid-acquisition.
"""

from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union

import numpy as np


class IncrementalStackWriter:
    """Write raw acquisition frames to disk one at a time (HDF5 or Zarr).

    Format is chosen from the extension: ``.h5``/``.hdf5`` -> HDF5,
    ``.zarr`` -> Zarr. The dataset is created lazily on the first
    :meth:`append` (so frame shape/dtype are taken from the real camera
    frame, preserved exactly - no JPEG, no 8-bit downcast). Chunked per
    angle ``(1, H, W)`` for lazy slice access.

    Parameters
    ----------
    path : str
        Output file/store path.
    n_frames : int
        Total number of frames (angles) to be written.
    axes : str
        Axis label stored as metadata (default ``"QYX"`` = angle, X, Y_lab).
    """

    def __init__(self, path: str, n_frames: int, axes: str = "QYX"):
        self.path = str(path)
        self.n_frames = n_frames
        self.axes = axes
        low = self.path.lower()
        if low.endswith((".h5", ".hdf5")):
            self.fmt = "hdf5"
        elif low.endswith(".zarr"):
            self.fmt = "zarr"
        else:
            self.fmt = "hdf5"
            self.path += ".h5"
        self._handle = None   # h5py.File or None
        self._ds = None       # dataset / zarr array
        self._i = 0

    def _create(self, frame: np.ndarray):
        shape = (self.n_frames,) + frame.shape
        chunks = (1,) + frame.shape
        if self.fmt == "hdf5":
            import h5py
            self._handle = h5py.File(self.path, "w")
            self._ds = self._handle.create_dataset(
                "frames", shape=shape, dtype=frame.dtype,
                chunks=chunks, compression="gzip",
            )
            self._ds.attrs["axes"] = self.axes
            self._ds.attrs["description"] = "LSFT raw acquisition frames"
        else:
            import zarr
            self._ds = zarr.open(self.path, mode="w", shape=shape,
                                 chunks=chunks, dtype=frame.dtype)
            self._ds.attrs["axes"] = self.axes
            self._ds.attrs["description"] = "LSFT raw acquisition frames"

    def append(self, frame: np.ndarray) -> None:
        frame = np.asarray(frame)
        if self._ds is None:
            self._create(frame)
        if self._i >= self.n_frames:
            raise IndexError("More frames appended than n_frames.")
        self._ds[self._i] = frame
        self._i += 1

    def close(self) -> str:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        return self.path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def write_tiff(
    path: str,
    data: Any,
    meta: dict,
) -> List[str]:
    """Write image layer to TIFF file."""
    import tifffile

    path = str(path)
    if not path.lower().endswith((".tif", ".tiff")):
        path += ".tif"

    arr = np.asarray(data)
    tifffile.imwrite(
        path,
        arr.astype(np.float32),
        imagej=True,
        metadata={"spacing": 1, "unit": "um", "axes": "ZYX"},
    )
    return [path]


def write_hdf5(
    path: str,
    data: Any,
    meta: dict,
) -> List[str]:
    """Write image layer to HDF5 file."""
    import h5py

    path = str(path)
    if not path.lower().endswith((".h5", ".hdf5")):
        path += ".h5"

    arr = np.asarray(data)
    with h5py.File(path, "w") as f:
        f.create_dataset(
            "reconstruction",
            data=arr.astype(np.float32),
            compression="gzip",
        )
        f.attrs["description"] = "LSFT reconstruction"
        f.attrs["shape_order"] = "X, Y_sample, Z_sample"

    return [path]


def write_zarr(
    path: str,
    data: Any,
    meta: dict,
) -> List[str]:
    """Write image layer to a Zarr store.

    The reconstructed volume is (X, Y_sample, Z_sample); we chunk per
    X-slice so consumers can lazily load individual cross-sections.
    Works on both zarr v2 and v3.
    """
    import zarr

    path = str(path)
    if not path.lower().endswith(".zarr"):
        path += ".zarr"

    arr = np.asarray(data).astype(np.float32)
    chunks = (1,) + arr.shape[1:] if arr.ndim >= 1 else None

    z = zarr.open(path, mode="w", shape=arr.shape, chunks=chunks, dtype=arr.dtype)
    z[:] = arr
    z.attrs["description"] = "LSFT reconstruction"
    z.attrs["shape_order"] = "X, Y_sample, Z_sample"

    return [path]
