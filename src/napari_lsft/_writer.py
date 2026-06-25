"""
Writer module for exporting reconstructed volumes.
"""

from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union

import numpy as np


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
