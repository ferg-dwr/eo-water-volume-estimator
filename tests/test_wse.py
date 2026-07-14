"""Tests for the swappable WSE-estimator port."""

from datetime import datetime, timezone

import numpy as np
import pytest

from eo_water_volume import estimate_volume
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


def test_registry_keys_match_model_ids():
    from eo_water_volume.wse import MODEL_REGISTRY

    for model_id, cls in MODEL_REGISTRY.items():
        assert cls.MODEL_ID == model_id


def test_every_concrete_estimator_is_registered():
    # Adding an estimator without registering it (and documenting it in
    # MODELS.md) should fail CI, not drift silently. Walks subclasses
    # recursively (v2 is a grandchild of the ABC) and requires each class
    # to DECLARE its own MODEL_ID, not inherit its parent's.
    from eo_water_volume.wse import MODEL_REGISTRY, WseEstimator

    def walk(cls):
        for c in cls.__subclasses__():
            yield c
            yield from walk(c)

    concrete = {c for c in walk(WseEstimator) if "MODEL_ID" in vars(c)}
    assert concrete == set(MODEL_REGISTRY.values())


def _tilted_valley(n=400):
    # V-ish valley with an along-axis bed slope and a TILTED true water surface.
    rows = np.arange(n) + 0.5
    cols = np.arange(n) - n / 2
    valley = 0.001 * np.abs(cols) ** 2 * 0.05
    bed = (-0.002 * rows)[:, None] + valley[None, :] + 3.0
    true_wse = 4.5 - 0.0012 * rows
    water = (bed < true_wse[:, None]).astype(float)
    return bed, water, true_wse


def test_profile_recovers_tilted_surface():
    from eo_water_volume.wse import ShorelineProfileWse

    bed, water, true_wse = _tilted_valley()
    f = ShorelineProfileWse().estimate(bed, water, region=np.ones(bed.shape, bool))

    assert not f.is_flat
    err = np.abs(f.values[:, 0] - true_wse)
    assert err.max() < 0.05  # tracks the true tilt to a few cm
    # fitted slope matches the truth (-0.0012 per row / 30 m pixels)
    assert abs(f.diagnostics["profile_slope_m_per_m"] - (-0.0012 / 30.0)) < 2e-6


def test_profile_anchoring_shifts_through_gauge():
    from eo_water_volume.wse import ShorelineProfileWse

    bed, water, true_wse = _tilted_valley()
    r = GaugeReading(
        "LIS",
        datetime(2026, 1, 15, 2, tzinfo=timezone.utc),
        0.0,
        float(true_wse[200]),
    )
    f = ShorelineProfileWse(anchor=r, gauge_row=200).estimate(
        bed, water, region=np.ones(bed.shape, bool)
    )
    # anchored profile passes through the gauge value at the gauge row
    assert abs(f.values[200, 0] - true_wse[200]) < 0.03
    assert "profile_anchor_offset_m" in f.diagnostics
    # volume against direct integration of the TRUE surface
    v_est = estimate_volume(bed, water, f.values, 900.0)
    v_true = 900.0 * np.sum(np.clip(true_wse[:, None] - bed, 0, None) * water)
    assert abs(v_est - v_true) / v_true < 0.02


def test_profile_excludes_polygon_cut_edges():
    from eo_water_volume.wse import ShorelineProfileWse

    bed, water, true_wse = _tilted_valley()
    region = np.ones(bed.shape, bool)
    region[:, 300:] = False  # slice the AOI straight through the pool
    f = ShorelineProfileWse().estimate(bed, water, region=region)
    err = np.abs(f.values[:, 0] - true_wse)
    assert err.max() < 0.05  # cut edge contributed no fake samples


def test_profile_fails_loudly_on_sparse_shoreline():
    from eo_water_volume.wse import ShorelineProfileWse

    bed, water, _ = _tilted_valley()
    with pytest.raises(ValueError):
        ShorelineProfileWse(min_bin_samples=10**6).estimate(
            bed, water, region=np.ones(bed.shape, bool)
        )


def test_profile_requires_gauge_row_when_anchored():
    from eo_water_volume.wse import ShorelineProfileWse

    r = GaugeReading("LIS", datetime(2026, 1, 15, 2, tzinfo=timezone.utc), 13.31, 4.057)
    with pytest.raises(ValueError):
        ShorelineProfileWse(anchor=r)


def test_profile_v2_survives_in_pool_dry_holes():
    # Reproduces v1's real-world failure: dry holes inside open water (SAR
    # wind-roughening misses) create fake shorelines sampling subaqueous bed.
    from eo_water_volume.wse import ShorelineProfileWse, ShorelineProfileWseV2

    bed, water, true_wse = _tilted_valley()
    rng = np.random.default_rng(7)
    holes = np.zeros(bed.shape, bool)
    for _ in range(60):
        r0, c0 = rng.integers(150, 400), rng.integers(150, 260)
        holes[r0 : r0 + 2, c0 : c0 + 2] = True
    water_holed = np.where(holes, 0.0, water)
    region = np.ones(bed.shape, bool)

    f1 = ShorelineProfileWse().estimate(bed, water_holed, region=region)
    f2 = ShorelineProfileWseV2().estimate(bed, water_holed, region=region)
    e1 = np.abs(np.asarray(f1.values)[:, 0] - true_wse)
    e2 = np.abs(np.asarray(f2.values)[:, 0] - true_wse)
    assert e1.max() > 0.15  # v1 is visibly corrupted
    assert e2.max() < 0.06  # v2 recovers the true surface
    assert f2.diagnostics["profile_n_samples"] < f1.diagnostics["profile_n_samples"]


def test_gauge_diagnostics_fail_soft_without_shoreline():
    # A scene with no usable shoreline -- e.g. no water detected at all
    # (partial tile coverage over dry land) -- must not kill a gauge run:
    # the estimate needs no shoreline, only the diagnostic does. It reports
    # None instead of raising (the 2026-01-26 14:07Z batch failure).
    no_water = np.zeros((10, 10))
    bed = np.zeros((10, 10))
    f = GaugeWse(_reading()).estimate(bed, no_water)
    assert f.values == 4.057
    assert f.diagnostics["wse_perimeter_m"] is None
    assert f.diagnostics["wse_gauge_minus_perimeter_m"] is None
