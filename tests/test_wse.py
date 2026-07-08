"""Tests for the swappable WSE-estimator port."""

from datetime import datetime, timezone

import numpy as np

from eo_water_volume.gauges import GaugeReading
from eo_water_volume.wse import GaugeWse, PerimeterWse, WseField


def _bowl(px=30.0, r=3000.0, z0=1.0, h=5.0, n=401):
    k = (h - z0) / r**2
    xs = (np.arange(n) - n // 2) * px
    X, Y = np.meshgrid(xs, xs)
    bed = z0 + k * (X**2 + Y**2)
    return bed, (bed < h).astype("float64")


def _reading():
    return GaugeReading(
        "LIS", datetime(2026, 1, 15, 2, tzinfo=timezone.utc), 13.31, 4.057
    )


def test_perimeter_estimator_matches_legacy_function():
    bed, water = _bowl()
    f = PerimeterWse().estimate(bed, water)
    assert f.is_flat
    assert abs(f.values - 4.959) < 0.01  # known bowl perimeter value
    assert "perimeter" in f.method
    assert f.diagnostics["wse_perimeter_m"] == f.values


def test_gauge_estimator_carries_reading_and_gap():
    bed, water = _bowl()
    f = GaugeWse(_reading()).estimate(bed, water)
    assert f.values == 4.057
    assert "gauge LIS" in f.method and "13.31 ft" in f.method
    gap = f.diagnostics["wse_gauge_minus_perimeter_m"]
    assert abs(gap - (4.057 - f.diagnostics["wse_perimeter_m"])) < 1e-12


def test_summary_stats_same_keys_flat_and_grid():
    flat = WseField(values=4.0, method="x")
    grid = WseField(values=np.linspace(2.0, 4.0, 10)[None, :], method="y")
    kf, kg = flat.summary_stats(), grid.summary_stats()
    assert set(kf) == set(kg) == {"wse_m", "wse_min_m", "wse_max_m"}
    assert kf["wse_m"] == kf["wse_min_m"] == 4.0
    assert abs(kg["wse_m"] - 3.0) < 1e-12
    assert (kg["wse_min_m"], kg["wse_max_m"]) == (2.0, 4.0)
    assert not grid.is_flat and flat.is_flat
