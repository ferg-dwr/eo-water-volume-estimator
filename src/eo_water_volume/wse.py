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
    ) -> WseField:
        """Estimate the water surface for this scene.

        `bed` and `water` are the co-registered DEM and water-fraction grids;
        `when_utc` is the sensing instant for time-aware estimators (gauges).
        """


class PerimeterWse(WseEstimator):
    """Median DEM elevation along the mask shoreline. Self-contained; degrades
    where the mask edge is not a real shoreline (clouds, AOI cuts, levees)."""

    MODEL_ID = "wse-perimeter-v1"

    def estimate(self, bed, water, when_utc=None) -> WseField:
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

    def estimate(self, bed, water, when_utc=None) -> WseField:
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


# --- model registry -----------------------------------------------------------
# Single machine-readable source of truth for WSE-estimator identity. The
# human-readable companion is MODELS.md at the repo root (table of
# assumptions, diagnostics, and failure modes, plus the "adding a model"
# recipe). Versioning rule: any behavior change to an estimator is a NEW
# MODEL_ID (v1 -> v2); old outputs stay interpretable forever.
MODEL_REGISTRY: dict[str, type[WseEstimator]] = {
    PerimeterWse.MODEL_ID: PerimeterWse,
    GaugeWse.MODEL_ID: GaugeWse,
}
