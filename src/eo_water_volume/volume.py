"""Water-volume core.

Volume of water under a planar surface, given a bed-elevation raster (DEM) and a
water mask. The whole estimate is one masked reduction:

    V = pixel_area * sum_i( water_i * max(wse - bed_i, 0) )

Pure numpy, no I/O. Grids are assumed co-registered (same shape/CRS/pixel size);
alignment lives in the data layer, not here.

Units: bed and wse in metres (NAVD88 for the CNRA Bay-Delta DEM); pixel_area in
m^2; volume in m^3.
"""

from __future__ import annotations

import numpy as np


def estimate_volume(
    bed: np.ndarray,
    water: np.ndarray,
    wse: float,
    pixel_area: float,
) -> float:
    """Water volume under a planar surface at elevation `wse`.

    Parameters
    ----------
    bed : 2-D float array
        Bed / ground elevation per pixel (DEM). NaN marks no-data.
    water : 2-D array
        Water coverage per pixel, interpreted as a fraction in [0, 1]. Pass a
        boolean/0-1 mask for open water, or a fractional layer for sub-pixel
        edges. Values outside [0, 1] are clipped.
    wse : float
        Water-surface elevation (planar-pool assumption), same datum as `bed`.
    pixel_area : float
        Ground area of one pixel, m^2 (e.g. 30 m * 30 m = 900).

    Returns
    -------
    float
        Volume in m^3.
    """
    # Delegates to volume_map so the scalar and the map can never drift.
    return float(np.sum(volume_map(bed, water, wse, pixel_area)))


def wse_from_perimeter(bed: np.ndarray, water: np.ndarray) -> float:
    """Estimate a scalar water-surface elevation from the mask shoreline.

    Depth goes to zero at the water's edge, so bed elevation along the mask
    boundary approximates the water surface. Median over boundary pixels for
    robustness against steep banks and misclassified edges.

    Needs no gauge or altimetry. Valid for a single quasi-static pool; NOT valid
    where WSE varies spatially (tidal channel networks).
    """
    bed = np.asarray(bed, dtype="float64")
    wet = np.asarray(water, dtype="float64") > 0.5

    dry_neighbour = np.zeros_like(wet)
    dry_neighbour[:-1, :] |= ~wet[1:, :]
    dry_neighbour[1:, :] |= ~wet[:-1, :]
    dry_neighbour[:, :-1] |= ~wet[:, 1:]
    dry_neighbour[:, 1:] |= ~wet[:, :-1]
    edge = np.zeros_like(wet)
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True

    boundary = wet & (dry_neighbour | edge)
    edge_bed = bed[boundary]
    edge_bed = edge_bed[np.isfinite(edge_bed)]
    if edge_bed.size == 0:
        raise ValueError("No valid boundary pixels; check mask/DEM alignment.")
    return float(np.median(edge_bed))


def summarize(
    bed: np.ndarray,
    water: np.ndarray,
    wse: float,
    pixel_area: float,
    invalid: np.ndarray | None = None,
) -> dict:
    """Volume plus the diagnostics you want to sanity-check a run.

    `invalid` is an optional boolean mask of unclassifiable pixels (sensor
    could not see: cloud, layover/shadow, HAND, fill). They contribute zero
    volume, so the returned `invalid_fraction` (share of all pixels in the
    scene) is the run's honesty metric: a large value means the volume is a
    known undercount. When `invalid` is not provided, `invalid_fraction` is
    None ("not assessed"), never 0.0 ("assessed and clean")."""
    frac = np.clip(np.asarray(water, dtype="float64"), 0.0, 1.0)
    frac[~np.isfinite(frac)] = 0.0
    depth = np.clip(wse - np.asarray(bed, "float64"), 0.0, None)
    depth[~np.isfinite(bed)] = 0.0
    wet_area = float(pixel_area * np.sum(frac))
    vol = estimate_volume(bed, water, wse, pixel_area)
    if invalid is not None:
        inv = np.asarray(invalid, dtype=bool)
        invalid_fraction = float(inv.sum() / inv.size) if inv.size else 0.0
    else:
        invalid_fraction = None
    return {
        "volume_m3": vol,
        "volume_acre_ft": vol / 1233.4818375475,  # DWR-facing units
        "wet_area_m2": wet_area,
        "wse_m": float(wse),
        "mean_depth_m": vol / wet_area if wet_area > 0 else 0.0,
        "max_depth_m": float(np.nanmax(depth)) if depth.size else 0.0,
        "invalid_fraction": invalid_fraction,
    }

def volume_map(
    bed: np.ndarray,
    water: np.ndarray,
    wse: float,
    pixel_area: float,
) -> np.ndarray:
    """Per-pixel water volume (m^3): the estimate_volume() reduction, un-summed.

    Returns a 2-D float64 grid where cell (i, j) holds
    pixel_area * water_ij * max(wse - bed_ij, 0); dry ground and no-data DEM
    cells are 0. By construction estimate_volume() == volume_map().sum(), so a
    map and its headline number are always consistent. Pair the result with the
    mask Raster's transform/crs to write a georeferenced product.
    """
    bed = np.asarray(bed, dtype="float64")
    frac = np.clip(np.asarray(water, dtype="float64"), 0.0, 1.0)

    depth = wse - bed  # signed depth
    np.clip(depth, 0.0, None, out=depth)  # dry ground -> 0
    depth[~np.isfinite(bed)] = 0.0  # no-data DEM contributes nothing
    frac[~np.isfinite(frac)] = 0.0

    return pixel_area * frac * depth