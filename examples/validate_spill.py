"""Independent spill-indicator check for a Yolo Bypass volume run.

Cross-validates a DSWx-derived wet-area pattern against the gauge world:
Fremont Weir stage (CDEC FRE via the ferg-dwr `inundation` package) says
whether Sacramento River water was spilling INTO the bypass around the
granule date. Fremont is the ENTRANCE indicator -- it does not measure
in-bypass WSE -- so this validates the *regime* (flooding vs. between
pulses), not the volume.

Requires the `inundation` package (not on PyPI):
    pip install -e /path/to/ferg-dwr/inundation

Usage:
    python examples/validate_spill.py [--date YYYY-MM-DD] [--wet-km2 X]

With no --date, uses the sensing date of the newest downloaded granule.
`calc_inundation()` is consulted when the date falls inside its coverage
(it inner-joins Dayflow, which publishes annually with lag); otherwise the
check stands on get_fre() + the post-2016-10-03 threshold directly, with
the same QC the package applies (2 < stage < 41.03 ft, daily max).
"""

from __future__ import annotations

import argparse
import glob
import re
from datetime import date, datetime, timedelta
from pathlib import Path

THRESHOLD_FT = 32.0  # Fremont spill threshold on/after 2016-10-03
THRESHOLD_FT_PRE = 33.5  # before 2016-10-03
LOOKBACK_DAYS = 45  # recession context window

REPO = Path(__file__).resolve().parents[1]
DOWNLOAD_DIR = REPO / "data/downloads/yolo"


def newest_granule_date() -> date:
    hits = sorted(glob.glob(str(DOWNLOAD_DIR / "*B01_WTR*.tif")))
    if not hits:
        raise SystemExit("No downloaded granules; pass --date instead.")
    m = re.search(r"_(\d{8})T\d{6}Z_", Path(hits[-1]).name)
    return datetime.strptime(m.group(1), "%Y%m%d").date()


def threshold_for(d: date) -> float:
    return THRESHOLD_FT if d >= date(2016, 10, 3) else THRESHOLD_FT_PRE


def daily_max_stage(fre, start: date, end: date):
    """QC + daily-max aggregation, mirroring inundation.calc_inundation."""
    import pandas as pd

    df = fre.copy()
    df["date"] = pd.to_datetime(df["datetime"]).dt.normalize()
    df = df[(df["value"] > 2) & (df["value"] < 41.03)]
    daily = df.groupby("date")["value"].max()
    idx = pd.date_range(start, end, freq="D")
    return daily.reindex(idx)


def spill_state(daily, when) -> dict:
    """Regime verdict for `when` from a daily-max stage series."""
    import pandas as pd

    when_ts = pd.Timestamp(when)
    thr = threshold_for(when)
    stage = daily.get(when_ts)
    window = daily.loc[:when_ts].dropna()
    over = window[window >= thr]
    last_spill = over.index.max() if len(over) else None
    days_since = (when_ts - last_spill).days if last_spill is not None else None
    return {
        "date": str(when),
        "stage_ft": None if stage is None or pd.isna(stage) else float(stage),
        "threshold_ft": thr,
        "spilling": bool(stage is not None and not pd.isna(stage) and stage >= thr),
        "last_spill_date": None if last_spill is None else str(last_spill.date()),
        "days_since_spill": days_since,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date())
    ap.add_argument("--wet-km2", type=float, help="wet area from the volume run")
    args = ap.parse_args()
    when = args.date or newest_granule_date()

    try:
        from inundation import calc_inundation, get_fre
    except ImportError:
        raise SystemExit(
            "The `inundation` package is required: pip install -e <path-to-repo>"
        )

    import pandas as pd

    start = when - timedelta(days=LOOKBACK_DAYS)
    fre = get_fre(start=str(start), end=str(when + timedelta(days=1)))
    state = spill_state(daily_max_stage(fre, start, when), when)

    print(f"--- Fremont Weir spill check for {state['date']} ---")
    print(
        f"stage (daily max)   : {state['stage_ft']} ft "
        f"(threshold {state['threshold_ft']} ft)"
    )
    print(f"spilling            : {state['spilling']}")
    print(
        f"last spill <= date  : {state['last_spill_date']} "
        f"(days since: {state['days_since_spill']}, "
        f"lookback {LOOKBACK_DAYS} d)"
    )

    # enrichment when Dayflow coverage allows
    try:
        inun = calc_inundation()
        row = inun[inun["date"] == pd.Timestamp(when)]
        if len(row):
            r = row.iloc[0]
            print(
                f"calc_inundation     : inundation={int(r['inundation'])}, "
                f"inund_days={int(r['inund_days'])}"
            )
        else:
            print("calc_inundation     : date beyond Dayflow coverage (expected lag)")
    except Exception as e:  # enrichment must never break the check
        print(f"calc_inundation     : unavailable ({e})")

    if state["spilling"]:
        regime = "SPILLING -- expect broad floodplain inundation"
    elif state["days_since_spill"] is not None and state["days_since_spill"] <= 14:
        regime = (
            f"RECESSION -- spill ended {state['days_since_spill']} d ago; expect "
            "elevated stages, inundated vegetation, intermediate wet share"
        )
    else:
        regime = "BASEFLOW -- expect perennial Toe Drain + tidal south only"
    print(f"\nregime: {regime}")
    if args.wet_km2 is not None:
        share = args.wet_km2 / 243.9
        print(f"wet area {args.wet_km2:.1f} km2 = {share:.0%} of the bypass")


if __name__ == "__main__":
    main()
