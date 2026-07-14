"""Wet-season storage hydrograph: every DSWx-S1 granule -> volume time series.

Batch companion to run_yolo_volume.py: fetches ALL granules in TEMPORAL
(not just the first), runs the primary gauge-anchored model per scene, and
writes a time-series CSV with the uncertainty budget per date. Scenes fail
individually (bad granule, gauge gap) without killing the season.

Run from the repo root:  python examples/run_yolo_timeseries.py
Reuses run_yolo_volume's fetch/analyze machinery and its config block.

Output: data/outputs/yolo_timeseries_<model>.csv with one row per scene:
sensing time, volume, acre-ft, wet area, WSE (+ gauge diagnostics), the
uncertainty terms, and the product paths. ALL granules are kept, including
reprocessed duplicates (identical sensing time, newer processing time --
identical rows) and same-day second passes (different sensing times --
genuinely different measurements): dedup policy belongs to the consumer,
who has the full granule name to decide with. Naive means over the raw CSV
double-weight reprocessed scenes.
"""

from __future__ import annotations

import csv
import glob
from pathlib import Path

from run_yolo_volume import (
    DEM_PATH,
    DOWNLOAD_DIR,
    OUTPUT_DIR,
    TEMPORAL,
    analyze,
    load_aoi,
    overpass_time_utc,
)

from eo_water_volume.contract import AOI, requests_for_volume
from eo_water_volume.gauges import CdecStation
from eo_water_volume.wse import GaugeWse

TILE = "_T10SFH_"  # covers 100% of the AOI; skip edge tiles from the bbox search

FIELDS = [
    "granule",
    "time_utc",
    "volume_m3",
    "volume_acre_ft",
    "wet_area_m2",
    "wse_m",
    "wse_model_id",
    "gauge_station",
    "gauge_time_utc",
    "wse_gauge_minus_perimeter_m",
    "invalid_fraction_aoi",
    "wse_perimeter_invalid_fraction",
    "unc_partial_m3",
    "unc_invalid_m3",
    "unc_wse_distance_scenario_m3",
    "unc_method_spread_m3",
    "unc_total_m3",
    "unc_total_fraction_of_volume",
    "volume_map",
]


def fetch_all(aoi: AOI) -> list[Path]:
    """Every WTR granule in TEMPORAL for the covering tile (cache-aware)."""
    import earthaccess

    need = requests_for_volume(aoi, *TEMPORAL)[0]
    earthaccess.login()
    results = earthaccess.search_data(
        short_name=need.short_name(),
        bounding_box=need.aoi.as_bbox(),
        temporal=(need.start_date.isoformat(), need.end_date.isoformat()),
    )
    if not results:
        raise SystemExit("No DSWx-S1 granules found; widen TEMPORAL.")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    have = {Path(p).name for p in glob.glob(str(DOWNLOAD_DIR / "*B01_WTR*.tif"))}
    missing = [
        r
        for r in results
        if not any(TILE in link and Path(link).name in have for link in r.data_links())
    ]
    if missing:
        print(f"{len(results)} granules found; downloading {len(missing)} new.")
        earthaccess.download(missing, local_path=str(DOWNLOAD_DIR))
    wtrs = sorted(
        Path(p)
        for p in glob.glob(str(DOWNLOAD_DIR / "*B01_WTR*.tif"))
        if TILE in Path(p).name
    )
    return wtrs


def already_done(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()
    with open(csv_path, newline="") as f:
        return {row["granule"] for row in csv.DictReader(f)}


def append_row(csv_path: Path, row: dict) -> None:
    new_file = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerow(row)


def main() -> None:
    aoi_geom, aoi_bbox = load_aoi()
    wtrs = fetch_all(aoi_bbox)
    print(f"{len(wtrs)} scene(s) on tile {TILE.strip('_')} in {TEMPORAL}")

    gauge = CdecStation()
    csv_path = OUTPUT_DIR / "yolo_timeseries_wse-gauge-v1.csv"
    done = already_done(csv_path)

    ok = skipped = failed = 0
    for wtr in wtrs:
        if wtr.name in done:
            skipped += 1
            continue
        try:
            when = overpass_time_utc(wtr)
            reading = gauge.wse_navd88_m(when)
            est = GaugeWse(reading)
            stats = analyze(wtr, DEM_PATH, aoi_geom, OUTPUT_DIR, estimator=est)
            stats["granule"] = wtr.name
            stats["time_utc"] = when.isoformat()
            append_row(csv_path, stats)
            ok += 1
            print(
                f"  {when.date()}  {stats['volume_m3']:>14,.0f} m^3  "
                f"({stats['volume_acre_ft']:>9,.0f} af)  "
                f"wse {stats['wse_m']:.3f}"
            )
        except Exception as err:  # per-scene isolation: log and continue
            failed += 1
            print(f"  FAILED {wtr.name}: {err}")

    print(f"\nseason: {ok} processed, {skipped} already done, {failed} failed")
    print(f"time series -> {csv_path}")


if __name__ == "__main__":
    main()
