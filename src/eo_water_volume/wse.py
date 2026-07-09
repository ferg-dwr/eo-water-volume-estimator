"""Water-surface-elevation (WSE) estimation as a swappable port.

The volume core accepts either a scalar WSE (flat pool) or a per-pixel WSE
grid (tilted/arbitrary surface) -- numpy broadcasting makes them the same
code path. This module makes the *choice of method* a first-class, swappable
object instead of an if/else in the pipeline:

    PerimeterWse()          -- self-contained fallback: DEM elevation along the
                               mask shoreline (median). No gauge needed.
    GaugeWse(reading)       -- flat pool anchored to one datum-corrected gauge
                               reading (NAVD88 m).
    (M3, next)  ShorelineProfileWse -- shoreline-sampled tilt, gauge-anchored.
    (M3, later) TwoGaugeTilt        -- linear tilt between two live anchors.

Every estimator returns a WseField that names its own method and carries its
diagnostics, so output provenance is always self-describing and comparing
methods on the same scene is a loop, not a rewrite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from .gauges import GaugeReading
from .volume import wse_from_perimeter


@dataclass(frozen=True)
class WseField:
    """A water-surface elevation: scalar (flat) or per-pixel grid (tilted).

    `method` is a short self-description destined for provenance tags;
    `diagnostics` carries method-specific honesty numbers (sample counts,
    anchor residuals, the perimeter estimate, ...).
    """

    values: float | np.ndarray
    method: str
    diagnostics: dict = field(default_factory=dict)

    @property
    def is_flat(self) -> bool:
        return np.isscalar(self.values) or np.ndim(self.values) == 0

    def summary_stats(self) -> dict:
        """wse_m plus min/max -- identical keys whether flat or a grid."""
        if self.is_flat:
            v = float(self.values)
            return {"wse_m": v, "wse_min_m": v, "wse_max_m": v}
        arr = np.asarray(self.values, dtype="float64")
        return {
            "wse_m": float(np.nanmean(arr)),
            "wse_min_m": float(np.nanmin(arr)),
            "wse_max_m": float(np.nanmax(arr)),
        }


class WseEstimator(ABC):
    """The interface the pipeline depends on for a water surface."""

    @abstractmethod
    def estimate(
        self,
        bed: np.ndarray,
        water: np.ndarray,
        when_utc: datetime | None = None,
        region: np.ndarray | None = None,
    ) -> WseField:
        """Estimate the water surface for this scene.

        `bed` and `water` are the co-registered DEM and water-fraction grids;
        `when_utc` is the sensing instant for time-aware estimators (gauges);
        `region` is an optional boolean in-AOI mask so estimators that read
        the shoreline can ignore polygon-cut edges (others may ignore it).
        """


class PerimeterWse(WseEstimator):
    """Median DEM elevation along the mask shoreline. Self-contained; degrades
    where the mask edge is not a real shoreline (clouds, AOI cuts, levees)."""

    MODEL_ID = "wse-perimeter-v1"

    def estimate(self, bed, water, when_utc=None, region=None) -> WseField:
        wse = wse_from_perimeter(bed, water)
        return WseField(
            values=wse,
            method="mask-perimeter median (no gauge)",
            diagnostics={"wse_perimeter_m": wse},
        )


class GaugeWse(WseEstimator):
    """Flat pool at one datum-corrected gauge reading (NAVD88 m).

    Carries the perimeter estimate as a diagnostic: the gauge-perimeter gap is
    a per-scene data-quality signal (Jan 15 2026 Yolo run: +0.565 m).
    """

    MODEL_ID = "wse-gauge-v1"

    def __init__(self, reading: GaugeReading):
        self.reading = reading

    def estimate(self, bed, water, when_utc=None, region=None) -> WseField:
        r = self.reading
        perim = wse_from_perimeter(bed, water)
        return WseField(
            values=r.wse_navd88_m,
            method=(
                f"gauge {r.station} @ {r.time_utc.isoformat()} "
                f"({r.stage_ft} ft NAVD88)"
            ),
            diagnostics={
                "wse_perimeter_m": perim,
                "wse_gauge_minus_perimeter_m": r.wse_navd88_m - perim,
                "gauge_station": r.station,
                "gauge_time_utc": r.time_utc.isoformat(),
            },
        )


class ShorelineProfileWse(WseEstimator):
    """Tilted WSE from the mask shoreline, optionally gauge-anchored.

    The DEM elevation along the water's edge is a local WSE sample wherever
    the edge is a real shoreline. This estimator collects those samples as a
    function of along-axis position (image ROW -- rows are northing-ordered
    at fixed pixel size, so the estimator needs no georeferencing), fits a
    robust profile (per-bin medians, lightly smoothed, interpolated across
    gaps), and returns a per-pixel WSE grid. With an anchor reading the whole
    profile is shifted so it passes through the gauge (shape from the
    DEM+mask, level from the gauge); unanchored it is the tilted sibling of
    PerimeterWse.

    Polygon-cut edges are excluded via `region`: a shoreline sample requires
    a dry neighbor that is INSIDE the region -- water cut by the AOI boundary
    is not a shoreline.
    """

    MODEL_ID = "wse-profile-v1"

    def __init__(
        self,
        anchor: GaugeReading | None = None,
        gauge_row: int | None = None,
        pixel_size_m: float = 30.0,
        bin_rows: int = 33,  # ~1 km bins at 30 m pixels
        min_bin_samples: int = 10,
    ):
        if anchor is not None and gauge_row is None:
            raise ValueError("anchoring requires gauge_row (the gauge's image row)")
        self.anchor = anchor
        self.gauge_row = gauge_row
        self.pixel_size_m = pixel_size_m
        self.bin_rows = bin_rows
        self.min_bin_samples = min_bin_samples

    def _shoreline(self, bed, wet, region):
        dry_inside = (~wet) & region
        near_dry = np.zeros_like(wet)
        near_dry[:-1, :] |= dry_inside[1:, :]
        near_dry[1:, :] |= dry_inside[:-1, :]
        near_dry[:, :-1] |= dry_inside[:, 1:]
        near_dry[:, 1:] |= dry_inside[:, :-1]
        shore = wet & region & near_dry & np.isfinite(bed)
        return shore

    def estimate(self, bed, water, when_utc=None, region=None) -> WseField:
        bed = np.asarray(bed, dtype="float64")
        wet = np.asarray(water, dtype="float64") > 0.5
        region = np.ones_like(wet) if region is None else np.asarray(region, dtype=bool)

        shore = self._shoreline(bed, wet, region)
        rows_idx, _ = np.nonzero(shore)
        samples = bed[shore]
        if samples.size < self.min_bin_samples:
            raise ValueError(
                f"Only {samples.size} shoreline samples; cannot fit a profile."
            )

        nrows = bed.shape[0]
        nbins = max(1, nrows // self.bin_rows)
        edges = np.linspace(0, nrows, nbins + 1)
        centers, medians = [], []
        for b in range(nbins):
            in_bin = (rows_idx >= edges[b]) & (rows_idx < edges[b + 1])
            if in_bin.sum() >= self.min_bin_samples:
                centers.append(0.5 * (edges[b] + edges[b + 1]))
                medians.append(float(np.median(samples[in_bin])))
        if len(centers) < 2:
            raise ValueError(
                f"Only {len(centers)} populated profile bin(s); need >= 2 "
                "(shoreline too sparse for a tilt -- use PerimeterWse/GaugeWse)."
            )
        centers = np.asarray(centers)
        medians = np.asarray(medians)
        if len(medians) >= 3:  # light smoothing: 3-bin moving average, edges kept
            sm = medians.copy()
            sm[1:-1] = (medians[:-2] + medians[1:-1] + medians[2:]) / 3.0
            medians = sm

        row_axis = np.arange(nrows) + 0.5
        profile = np.interp(row_axis, centers, medians)  # clamps beyond end bins

        resid = samples - np.interp(rows_idx + 0.5, centers, medians)
        slope_m_per_m = float(np.polyfit(centers * self.pixel_size_m, medians, 1)[0])
        diagnostics = {
            "wse_perimeter_m": float(np.median(samples)),
            "profile_n_samples": int(samples.size),
            "profile_n_bins": int(len(centers)),
            "profile_slope_m_per_m": slope_m_per_m,
            "profile_residual_mad_m": float(np.median(np.abs(resid))),
        }

        anchored = ""
        if self.anchor is not None:
            at_gauge = float(np.interp(self.gauge_row + 0.5, centers, medians))
            offset = self.anchor.wse_navd88_m - at_gauge
            profile = profile + offset
            diagnostics.update(
                {
                    "profile_at_gauge_unanchored_m": at_gauge,
                    "profile_anchor_offset_m": offset,
                    "gauge_station": self.anchor.station,
                    "gauge_time_utc": self.anchor.time_utc.isoformat(),
                }
            )
            anchored = (
                f", anchored through {self.anchor.station} "
                f"@ {self.anchor.wse_navd88_m:.3f} m"
            )

        grid = np.broadcast_to(profile[:, None], bed.shape)
        return WseField(
            values=grid,
            method=(
                f"shoreline profile ({len(centers)} bins, "
                f"slope {slope_m_per_m:+.2e} m/m{anchored})"
            ),
            diagnostics=diagnostics,
        )


# --- model registry -----------------------------------------------------------
# Single machine-readable source of truth for WSE-estimator identity. The
# human-readable companion is MODELS.md at the repo root (table of
# assumptions, diagnostics, and failure modes, plus the "adding a model"
# recipe). Versioning rule: any behavior change to an estimator is a NEW
# MODEL_ID (v1 -> v2); old outputs stay interpretable forever.
MODEL_REGISTRY: dict[str, type[WseEstimator]] = {
    PerimeterWse.MODEL_ID: PerimeterWse,
    GaugeWse.MODEL_ID: GaugeWse,
    ShorelineProfileWse.MODEL_ID: ShorelineProfileWse,
}
