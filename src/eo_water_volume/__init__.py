"""Water-volume estimation from EO water masks + a bathymetric DEM.

The base import pulls in only the pure-numpy core, so `import eo_water_volume`
needs no rasterio. Raster I/O lives in `eo_water_volume.sources`, imported on
demand.
"""

from .volume import estimate_volume, volume_map, wse_from_perimeter, summarize

__version__ = "0.2.0"
__all__ = [
    "estimate_volume",
    "wse_from_perimeter",
    "summarize",
    "volume_map",
    "__version__",
]
