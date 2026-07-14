"""Tests for the hydrograph utilities."""

import csv

import pytest

from eo_water_volume.hydrograph import load_timeseries, plot_hydrograph


def _write_csv(path, rows):
    fields = [
        "granule",
        "time_utc",
        "volume_m3",
        "volume_acre_ft",
        "wet_area_m2",
        "wse_m",
        "unc_total_m3",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _row(g, t, v):
    return {
        "granule": g,
        "time_utc": t,
        "volume_m3": v,
        "volume_acre_ft": v / 1233.48,
        "wet_area_m2": 8e7,
        "wse_m": 4.0,
        "unc_total_m3": v * 0.5,
    }


def test_load_timeseries_sorts_and_keeps_duplicates(tmp_path):
    p = tmp_path / "ts.csv"
    _write_csv(
        p,
        [
            _row("b", "2026-02-01T02:00:00+00:00", 2e8),
            _row("a", "2026-01-15T01:58:43+00:00", 1e8),
            _row("a2", "2026-01-15T01:58:43+00:00", 1e8),  # reprocessed duplicate
        ],
    )
    scenes = load_timeseries(p)
    assert [s.granule for s in scenes] == ["a", "a2", "b"]  # sorted, all kept
    assert scenes[0].volume_m3 == 1e8


def test_plot_hydrograph_returns_figure(tmp_path):
    mpl = pytest.importorskip("matplotlib")  # noqa: F841  ([viz] extra)
    import matplotlib

    matplotlib.use("Agg")
    p = tmp_path / "ts.csv"
    _write_csv(
        p,
        [
            _row("a", "2026-01-15T01:58:43+00:00", 1e8),
            _row("b", "2026-02-01T02:00:00+00:00", 2e8),
        ],
    )
    fig = plot_hydrograph(load_timeseries(p), acre_feet=True)
    assert fig.axes and fig.axes[0].has_data()


def test_plot_empty_raises():
    with pytest.raises(ValueError):
        plot_hydrograph([])
