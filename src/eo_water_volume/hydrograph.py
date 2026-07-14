"""Storage-hydrograph utilities over the time-series CSV.

Pure-stdlib loading (no pandas); plotting lazily imports matplotlib, which
lives behind the [viz] extra:  pip install -e ".[viz]"

Designed to be imported by notebooks and scripts alike:

    from eo_water_volume.hydrograph import load_timeseries, plot_hydrograph
    scenes = load_timeseries("data/outputs/yolo_timeseries_wse-gauge-v1.csv")
    fig = plot_hydrograph(scenes)          # -> matplotlib Figure
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Scene:
    granule: str
    time_utc: datetime
    volume_m3: float
    volume_acre_ft: float
    wet_area_m2: float
    wse_m: float
    unc_total_m3: float


def load_timeseries(csv_path: str | Path) -> list[Scene]:
    """All scenes from a time-series CSV, sorted by sensing time.

    Rows are returned as-is: reprocessed duplicates (identical sensing time)
    are NOT collapsed -- see the batch runner's docstring for the policy.
    """
    out: list[Scene] = []
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            out.append(
                Scene(
                    granule=row["granule"],
                    time_utc=datetime.fromisoformat(row["time_utc"]),
                    volume_m3=float(row["volume_m3"]),
                    volume_acre_ft=float(row["volume_acre_ft"]),
                    wet_area_m2=float(row["wet_area_m2"]),
                    wse_m=float(row["wse_m"]),
                    unc_total_m3=float(row["unc_total_m3"]),
                )
            )
    out.sort(key=lambda s: s.time_utc)
    return out


def plot_hydrograph(
    scenes: list[Scene],
    *,
    envelope: bool = True,
    acre_feet: bool = False,
    title: str = "Yolo Bypass storage hydrograph",
):
    """Volume vs time as a matplotlib Figure; requires the [viz] extra.

    `envelope=True` shades volume +/- the conservative uncertainty budget
    (a labeled envelope, not a confidence interval -- see uncertainty.py).
    """
    try:
        import matplotlib.pyplot as plt
    except ImportError as err:  # pragma: no cover - exercised without extra
        raise ImportError('plotting needs matplotlib: pip install -e ".[viz]"') from err

    if not scenes:
        raise ValueError("No scenes to plot.")
    scale = 1 / 1233.4818375475 if acre_feet else 1e-6
    unit = "acre-feet" if acre_feet else "million m$^3$"
    t = [s.time_utc for s in scenes]
    v = [s.volume_m3 * scale for s in scenes]

    fig, ax = plt.subplots(figsize=(9, 4.5))
    if envelope:
        lo = [max((s.volume_m3 - s.unc_total_m3) * scale, 0.0) for s in scenes]
        hi = [(s.volume_m3 + s.unc_total_m3) * scale for s in scenes]
        ax.fill_between(
            t,
            lo,
            hi,
            alpha=0.18,
            linewidth=0,
            label="conservative uncertainty envelope",
        )
    ax.plot(t, v, marker="o", markersize=4, linewidth=1.2, label="per-scene volume")
    ax.set_ylabel(f"volume, {unit}")
    ax.set_title(title)
    ax.set_ylim(bottom=0)
    ax.legend(loc="upper right", frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig
