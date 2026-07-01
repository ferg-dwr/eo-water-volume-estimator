"""Data-access seam (a "port"): the volume model reads rasters through this
interface and never knows whether they came from local disk, the toolkit, or S3.

`LocalFileSource` is the dev/default implementation, reading pre-downloaded
GeoTIFFs. A future `ToolkitSource` (calling eo/dwr-eo-toolkit) would implement
the same interface, so nothing downstream changes.

rasterio is imported lazily inside methods, so importing this module -- and the
package -- does not require the `[io]` extra.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

# OPERA DSWx-S1 WTR classes counted as water: 1 = open water, 2 = partial /
# inundated vegetation. Values >= 252 are invalid (HAND/layover/shadow/cloud/fill).
WATER_CLASSES: tuple[int, ...] = (1, 2)
INVALID_FROM: int = 252


@dataclass
class Raster:
    """A grid plus its georeferencing. `transform` is an affine.Affine."""

    data: np.ndarray
    transform: object
    crs: object

    @property
    def pixel_area(self) -> float:
        """Ground area of one pixel, m^2 (valid when the CRS is metric/UTM)."""
        return abs(self.transform.a * self.transform.e)


class WaterDataSource(ABC):
    """The interface the volume model depends on."""

    @abstractmethod
    def water_mask(self) -> Raster:
        """Water-classification raster (e.g. OPERA DSWx WTR)."""

    @abstractmethod
    def bathymetry(self, like: Raster | None = None) -> Raster:
        """Bed-elevation raster; if `like` is given, reprojected onto its grid."""


class LocalFileSource(WaterDataSource):
    """Reads a pre-downloaded water mask and DEM from disk. No network."""

    def __init__(self, mask_path: str, dem_path: str):
        self.mask_path = mask_path
        self.dem_path = dem_path

    def water_mask(self) -> Raster:
        import rasterio as rio

        with rio.open(self.mask_path) as src:
            return Raster(src.read(1), src.transform, src.crs)

    def bathymetry(self, like: Raster | None = None) -> Raster:
        import rasterio as rio
        from rio.enums import Resampling
        from rio.warp import reproject

        with rio.open(self.dem_path) as dem:
            if like is None:
                return Raster(dem.read(1).astype("float64"), dem.transform, dem.crs)
            # Average-resample onto the mask grid: the coarse cell keeps the mean
            # bed elevation, which is exactly what the volume integral needs.
            dst = np.full(like.data.shape, np.nan, dtype="float64")
            reproject(
                source=rio.band(dem, 1),
                destination=dst,
                src_transform=dem.transform,
                src_crs=dem.crs,
                dst_transform=like.transform,
                dst_crs=like.crs,
                resampling=Resampling.average,
                dst_nodata=np.nan,
            )
            return Raster(dst, like.transform, like.crs)

    def load_aligned(self) -> tuple[np.ndarray, np.ndarray, float]:
        """Return (water, bed, pixel_area) ready for volume.estimate_volume().

        `water` is 1.0 for water classes, 0.0 otherwise. Invalid OPERA pixels
        currently map to 0.0 (treated as dry) -- see ROADMAP for why that needs
        explicit invalid-fraction accounting before the number is trustworthy.
        """
        mask = self.water_mask()
        bed = self.bathymetry(like=mask)
        water = np.isin(mask.data, WATER_CLASSES).astype("float64")
        return water, bed.data, mask.pixel_area
