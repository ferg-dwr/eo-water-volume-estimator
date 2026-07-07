"""Georeferenced outputs: write model grids (volume maps, depth grids) to disk.

The writer takes a bare numpy array plus a `Raster` (the mask the model ran on)
and stamps the array with that raster's grid -- transform and CRS -- so the
output opens correctly in QGIS and is consumable by downstream tools such as
spatio_hydrograph. Keeping georeferencing here, not in volume.py, preserves the
split: the core stays pure math, I/O lives at the edges.

rasterio is imported lazily, so importing this module does not require the
`[io]` extra (same pattern as sources.py).
"""

from __future__ import annotations

import numpy as np

from .sources import Raster


def write_geotiff(
    data: np.ndarray,
    like: Raster,
    path: str,
    nodata: float | None = None,
    dtype: str = "float32",
    band_name: str | None = None,
    units: str | None = None,
    tags: dict | None = None,
) -> str:
    """Write `data` as a single-band GeoTIFF on `like`'s grid. Returns `path`.

    Parameters
    ----------
    data : 2-D array
        Grid to write (e.g. volume_map() output). Must match `like.data.shape`,
        i.e. the grid the model actually ran on.
    like : Raster
        Supplies the georeferencing (transform + crs).
    path : str
        Output filepath (.tif).
    nodata : float, optional
        Value to record as nodata in the file's metadata. The writer does not
        alter pixel values; it only declares which value *means* nodata.
    dtype : str
        On-disk dtype. float32 halves file size vs float64 and is standard for
        continuous rasters; pass "float64" to preserve full precision.
    band_name : str, optional
        Band description shown by GIS tools (e.g. "water_volume_m3").
    units : str, optional
        Band unit string recorded in the file (e.g. "m^3").
    tags : dict, optional
        Free-form dataset metadata (GDAL tags): run provenance such as wse,
        invalid_fraction, source granule, model version. Values are stringified.

    Note: the projection is carried by `like.crs` in the profile -- CRS *is*
    the file's projection metadata; nothing extra to add for that.
    """
    import rasterio

    data = np.asarray(data)
    if data.ndim != 2:
        raise ValueError(f"Expected a 2-D grid, got shape {data.shape}")
    if data.shape != like.data.shape:
        raise ValueError(
            f"Grid shape {data.shape} does not match the reference raster "
            f"{like.data.shape}; write on the grid the model ran on."
        )

    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": dtype,
        "crs": like.crs,
        "transform": like.transform,
        "compress": "deflate",
    }
    if nodata is not None:
        profile["nodata"] = nodata

    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data.astype(dtype), 1)
        if band_name is not None:
            dst.set_band_description(1, band_name)
        if units is not None:
            dst.units = (units,)
        if tags:
            dst.update_tags(**{k: str(v) for k, v in tags.items()})
    return path
