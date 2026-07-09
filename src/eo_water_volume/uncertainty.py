"""Per-pixel volume-uncertainty terms -- each with different epistemic standing.

Three term families, deliberately kept separate so a map or budget can say
*which kind* of not-knowing dominates where:

  partial_fraction_term  EXACT BOUND.  Class-2 pixels carry an assumed water
                         fraction (default 0.5); truth lies in [0, 1], so the
                         volume error is exactly +/- max(a, 1-a)*area*depth.
  invalid_term           EXACT BOUND.  Sensor-blind pixels contribute anywhere
                         in [0, area*max(wse-bed, 0)]; we count 0, so the
                         (one-sided) bound is the full column.
  wse_distance_term      SCENARIO.     A flat WSE anchored at one gauge is
                         exact there and degrades with along-axis distance at
                         a rate we cannot currently measure (southern gauges
                         dark, 2026-07 probe). slope_rate_m_per_m is an
                         explicit knob -- same epistemics as
                         PARTIAL_WATER_FRACTION: a labeled assumption. The
                         shoreline-profile estimator (M3) will replace the
                         scenario with a measured slope + fit residuals.

A fourth term -- method spread, the per-pixel |difference| between two
estimators' volume maps -- is measured, not modeled, and needs no function
here: it is volume_map(A) - volume_map(B), computed by the caller.

combine() SUMS terms rather than adding in quadrature: the terms are bounds
and scenarios, not independent Gaussians, and pretending otherwise would be
fake rigor. The result is a labeled conservative envelope.

Pure numpy; no I/O. The distance grid is supplied by the caller (it needs the
raster transform, which lives at the edges, not in the math).
"""

from __future__ import annotations

import numpy as np


def _depth(bed: np.ndarray, wse: float | np.ndarray) -> np.ndarray:
    d = np.asarray(wse, dtype="float64") - np.asarray(bed, dtype="float64")
    d = np.clip(d, 0.0, None)
    d[~np.isfinite(d)] = 0.0
    return d


def partial_fraction_term(
    bed: np.ndarray,
    wse: float | np.ndarray,
    pixel_area: float,
    partial: np.ndarray,
    assumed_fraction: float = 0.5,
) -> np.ndarray:
    """+/- volume (m^3) from the partial-class fraction assumption. EXACT.

    `partial` is a boolean mask of class-2 (partial-surface-water) pixels.
    With assumed fraction a and truth in [0, 1], the worst-case per-pixel
    error is max(a, 1-a) * pixel_area * depth.
    """
    half_width = max(assumed_fraction, 1.0 - assumed_fraction)
    return np.where(
        np.asarray(partial, bool), pixel_area * half_width * _depth(bed, wse), 0.0
    )


def invalid_term(
    bed: np.ndarray,
    wse: float | np.ndarray,
    pixel_area: float,
    invalid: np.ndarray,
) -> np.ndarray:
    """Upper-bound volume (m^3) hiding in sensor-blind pixels. EXACT, one-sided.

    We count invalid pixels as zero volume; the truth is in
    [0, pixel_area * max(wse - bed, 0)] -- a pure undercount bound.
    """
    return np.where(np.asarray(invalid, bool), pixel_area * _depth(bed, wse), 0.0)


def wse_distance_term(
    water: np.ndarray,
    pixel_area: float,
    distance_m: np.ndarray,
    slope_rate_m_per_m: float = 5e-5,
) -> np.ndarray:
    """+/- volume (m^3) from flat-WSE error growing with distance. SCENARIO.

    Error model: |dWSE| = slope_rate * distance from the anchoring gauge
    (along the bypass axis), so per-pixel volume error is
    pixel_area * water_fraction * slope_rate * distance. Linear in dWSE --
    i.e. it ignores the max(.,0) clip, so it mildly overstates where water is
    shallower than the implied dWSE. The default 5e-5 m/m (5 cm per km) is a
    placeholder scenario, NOT a measurement; label it wherever it is shown.
    """
    frac = np.clip(np.asarray(water, dtype="float64"), 0.0, 1.0)
    frac[~np.isfinite(frac)] = 0.0
    return (
        pixel_area * frac * slope_rate_m_per_m * np.asarray(distance_m, dtype="float64")
    )


def combine(*terms: np.ndarray) -> np.ndarray:
    """Conservative envelope: plain sum of term maps (not quadrature)."""
    out = np.zeros_like(np.asarray(terms[0], dtype="float64"))
    for t in terms:
        out = out + np.asarray(t, dtype="float64")
    return out


def budget(terms: dict[str, np.ndarray], volume_m3: float) -> dict:
    """Scene totals per term, the combined envelope, and shares of the volume."""
    out: dict = {}
    total = 0.0
    for name, grid in terms.items():
        t = float(np.nansum(grid))
        out[f"unc_{name}_m3"] = t
        total += t
    out["unc_total_m3"] = total
    out["unc_total_fraction_of_volume"] = total / volume_m3 if volume_m3 > 0 else None
    return out
