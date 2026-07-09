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
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

# Per-pixel water fraction assigned to each OPERA DSWx-S1 WTR class.
#   1 = open water        -> fully wet
#   2 = partial water / inundated vegetation
#   3 = inundated vegetation -> fully wet
# IMPORTANT: DSWx-S1 flags class 2 *categorically* -- it carries no sub-pixel
# fraction -- so PARTIAL_WATER_FRACTION below is a HEURISTIC, not a measurement.
# It is a single visible knob: set it to 1.0 to treat partial as fully wet
# (over-estimates), 0.0 to drop it and keep open water only (under-estimates),
# or leave the nominal 0.5. A *quantitative* sub-pixel fraction comes later from
# SWOT's water_fraction band, not from DSWx-S1.
PARTIAL_WATER_FRACTION: float = 0.5
DEFAULT_WATER_FRACTIONS = {
    1: 1.0,  # open water (DSWx-S1 and DSWx-HLS)
    2: 0.5,  # partial surface water -- DSWx-HLS only; explicit heuristic
    3: 1.0,  # inundated vegetation -- DSWx-S1; spec: "considered inundated"
}

INVALID_FROM = 250  # DSWx-S1: 250 HAND, 251 layover/shadow, 255 fill;
# DSWx-HLS masks (252+) also caught. Verified vs the
# OPERA DSWx-S1 Product Spec v1.0.0, 2026-07-09.


def invalid_mask_from_classes(wtr: np.ndarray) -> np.ndarray:
    """Boolean mask of pixels the sensor could not classify.

    DSWx-S1 codes >= INVALID_FROM (252) are HAND-masked, layover/shadow, cloud,
    or fill: the product says "no answer here", not "dry". Downstream, these
    contribute zero volume -- the invalid *fraction* is reported so a run over a
    partially masked scene carries its own honesty metric.
    """
    return np.asarray(wtr) >= INVALID_FROM


def water_fraction_from_classes(
    wtr: np.ndarray,
    fractions: Mapping[int, float] = DEFAULT_WATER_FRACTIONS,
) -> np.ndarray:
    """Map DSWx WTR class codes to a per-pixel water fraction in [0, 1].

    Classes absent from `fractions` -- including invalid codes (>= 252) -- map to
    0.0 (dry). Pure numpy; no rasterio, so it is unit-testable without a GeoTIFF.
    """
    out = np.zeros(wtr.shape, dtype="float64")
    for cls, frac in fractions.items():
        out[wtr == cls] = frac
    return out


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
        import rasterio

        with rasterio.open(self.mask_path) as src:
            return Raster(src.read(1), src.transform, src.crs)

    def bathymetry(self, like: Raster | None = None) -> Raster:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.warp import reproject

        with rasterio.open(self.dem_path) as dem:
            if like is None:
                return Raster(dem.read(1).astype("float64"), dem.transform, dem.crs)
            # Average-resample onto the mask grid: the coarse cell keeps the mean
            # bed elevation, which is exactly what the volume integral needs.
            dst = np.full(like.data.shape, np.nan, dtype="float64")
            reproject(
                source=rasterio.band(dem, 1),
                destination=dst,
                src_transform=dem.transform,
                src_crs=dem.crs,
                dst_transform=like.transform,
                dst_crs=like.crs,
                resampling=Resampling.average,
                dst_nodata=np.nan,
            )
            return Raster(dst, like.transform, like.crs)

    def load_aligned(
        self,
        fractions: Mapping[int, float] = DEFAULT_WATER_FRACTIONS,
    ) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
        """Return (water, bed, pixel_area, invalid) for the volume core.

        `water` is a per-pixel fraction in [0, 1] from `fractions` (open water
        1.0, partial a heuristic, invalid 0.0). `invalid` is a boolean mask of
        unclassifiable pixels (codes >= 252): they contribute zero volume, and
        passing them to volume.summarize() reports the invalid fraction so the
        undercount is visible instead of silent.
        """
        mask = self.water_mask()
        bed = self.bathymetry(like=mask)
        water = water_fraction_from_classes(mask.data, fractions)
        invalid = invalid_mask_from_classes(mask.data)
        return water, bed.data, mask.pixel_area, invalid
