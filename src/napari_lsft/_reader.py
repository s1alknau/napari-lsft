"""
Reader module for rotational light-sheet data.

Supports TIFF stacks, HDF5 files, and Zarr arrays.
Expected data shape: (n_angles, height, width) where
  - axis 0 = rotation angles
  - axis 1 = X (along capillary / light-sheet scan)
  - axis 2 = Y_lab (detector pixels perpendicular to capillary)
"""

from pathlib import Path
from typing import Callable, List, Optional, Union

import dask.array as da
import h5py
import numpy as np
import tifffile
import zarr


def napari_get_reader(
    path: Union[str, List[str]],
) -> Optional[Callable]:
    """Return reader function if path is a supported format."""
    if isinstance(path, list):
        path = path[0]
    path = Path(path)

    if path.suffix.lower() in (".tif", ".tiff"):
        return read_tiff
    elif path.suffix.lower() in (".h5", ".hdf5"):
        return read_hdf5
    elif path.is_dir() and (path / ".zarray").exists():
        return read_zarr
    elif path.suffix.lower() == ".zarr":
        return read_zarr
    return None


def read_tiff(path: Union[str, List[str]]):
    """Read a TIFF stack as (n_angles, height, width)."""
    if isinstance(path, list):
        path = path[0]
    data = tifffile.imread(str(path))
    meta = {
        "name": Path(path).stem,
        "metadata": {"source": str(path), "format": "tiff"},
    }
    return [(data, meta, "image")]


def read_hdf5(path: Union[str, List[str]]):
    """Read HDF5 file. Uses the first 3D dataset found or 'data' key."""
    if isinstance(path, list):
        path = path[0]
    path = str(path)

    with h5py.File(path, "r") as f:
        # Try common dataset names first
        for key in ("data", "images", "projections", "stack"):
            if key in f and f[key].ndim >= 3:
                data = f[key][:]
                break
        else:
            # Find first 3D dataset
            data = None
            def _find_3d(name, obj):
                nonlocal data
                if isinstance(obj, h5py.Dataset) and obj.ndim >= 3 and data is None:
                    data = obj[:]
            f.visititems(_find_3d)

        if data is None:
            raise ValueError(f"No 3D dataset found in {path}")

    meta = {
        "name": Path(path).stem,
        "metadata": {"source": path, "format": "hdf5"},
    }
    return [(data, meta, "image")]


def read_zarr(path: Union[str, List[str]]):
    """Read Zarr array as dask array for lazy loading."""
    if isinstance(path, list):
        path = path[0]
    path = str(path)

    z = zarr.open(path, mode="r")

    # If it's a group, find the first suitable array
    if isinstance(z, zarr.hierarchy.Group):
        for key in ("data", "images", "projections", "stack"):
            if key in z and z[key].ndim >= 3:
                z = z[key]
                break
        else:
            # Find first 3D array
            for key in z:
                if z[key].ndim >= 3:
                    z = z[key]
                    break

    data = da.from_zarr(z) if hasattr(z, "shape") else None
    if data is None:
        raise ValueError(f"No 3D dataset found in {path}")

    meta = {
        "name": Path(path).stem,
        "metadata": {"source": path, "format": "zarr"},
    }
    return [(data, meta, "image")]


def load_data_array(
    path: str, dataset_key: Optional[str] = None
) -> np.ndarray:
    """
    Utility to load data from any supported format into a numpy array.

    Parameters
    ----------
    path : str
        Path to data file.
    dataset_key : str, optional
        HDF5/Zarr dataset key. If None, auto-detect.

    Returns
    -------
    np.ndarray
        Data array of shape (n_angles, n_x, n_y_lab).
    """
    path = Path(path)

    if path.suffix.lower() in (".tif", ".tiff"):
        return tifffile.imread(str(path)).astype(np.float32)

    elif path.suffix.lower() in (".h5", ".hdf5"):
        with h5py.File(str(path), "r") as f:
            if dataset_key and dataset_key in f:
                return f[dataset_key][:].astype(np.float32)
            result = read_hdf5(str(path))
            return result[0][0].astype(np.float32)

    elif path.suffix.lower() == ".zarr" or path.is_dir():
        result = read_zarr(str(path))
        data = result[0][0]
        if isinstance(data, da.Array):
            data = data.compute()
        return data.astype(np.float32)

    raise ValueError(f"Unsupported format: {path.suffix}")
