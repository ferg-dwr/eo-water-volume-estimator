"""Wet-area cross-validation via spatio_hydrograph's vector pipeline.

Reads a published yolo_water product and computes the wet area two ways:
our pixel method (recomputed inline: fraction-weighted sum x pixel area)
and spatio_hydrograph's independent route (vectorize value==1 pixels into
polygons, sum the polygon geometry areas). Agreement means the product
survives contact with a real external consumer; disagreement localizes a
semantics bug (nodata, fractions, georeferencing).

Semantics note: spatio_hydrograph counts BINARY water (value == 1); our
wet_area_m2 is FRACTION-weighted. On DSWx-S1 products these coincide
(fractions are 0/1); on future DSWx-HLS products with 0.5 partial pixels
they will not, and both numbers are printed so the difference is visible.

Requires spatio_hydrograph (not on PyPI):
    pip install -e /path/to/ferg-dwr/spatio_hydrograph

Usage:
    python examples/validate_wet_area.py [--water-tif PATH]
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO / "data/outputs"
NODATA = -9999.0


def newest_water_product() -> Path:
    hits = sorted(glob.glob(str(OUTPUT_DIR / "yolo_water_*.tif")))
    if not hits:
        raise SystemExit("No yolo_water_*.tif products found; pass --water-tif.")
    return Path(hits[-1])


def pixel_side(path: Path) -> dict:
    """Our method, recomputed from the product itself."""
    import rasterio

    with rasterio.open(path) as src:
        vals = src.read(1)
        px_area = abs(src.transform.a * src.transform.e)
    valid = vals != NODATA
    frac = np.clip(np.where(valid, vals, 0.0), 0.0, 1.0)
    return {
        "pixel_area_m2": px_area,
        "wet_area_frac_weighted_m2": float(frac.sum() * px_area),
        "wet_area_binary1_m2": float((vals == 1.0).sum() * px_area),
        "n_partial_pixels": int(((vals > 0) & (vals < 1) & valid).sum()),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--water-tif", type=Path, default=None)
    args = ap.parse_args()
    tif = args.water_tif or newest_water_product()

    try:
        from spatio_hydrograph.config import Config
        from spatio_hydrograph.landscape_metrics import LandscapeMetrics
        from spatio_hydrograph.raster_processing import RasterProcessor
    except ImportError:
        raise SystemExit("spatio_hydrograph is required: pip install -e <path-to-repo>")

    print(f"product: {tif.name}")
    ours = pixel_side(tif)
    print(
        f"pixel method  : frac-weighted {ours['wet_area_frac_weighted_m2']:,.0f} m2 "
        f"| binary==1 {ours['wet_area_binary1_m2']:,.0f} m2 "
        f"| partial px {ours['n_partial_pixels']}"
    )

    config = Config(water_year=2026, output_dir=REPO / "data/outputs/spatio_tmp")
    processor = RasterProcessor(config)
    polys = processor.raster_to_polygons(tif)
    vector_total = float(np.asarray(polys["area_m2"].values, dtype=float).sum())
    print(f"vector method : {vector_total:,.0f} m2 across {len(polys)} patches")

    diff = vector_total - ours["wet_area_binary1_m2"]
    rel = abs(diff) / max(ours["wet_area_binary1_m2"], 1.0)
    print(f"binary-vs-vector difference: {diff:+,.0f} m2 ({rel:.3%})")
    verdict = (
        "AGREE (product is consumer-safe)"
        if rel < 0.005
        else "DISAGREE -- investigate nodata/CRS/fraction semantics"
    )
    print(f"verdict       : {verdict}")

    # operational view: their default small-patch filter
    filtered = processor.filter_by_area(polys, min_area_m2=5000.0)
    kept = float(np.asarray(filtered["area_m2"].values, dtype=float).sum())
    print(
        f"\noperational (>=5000 m2 patches): {kept:,.0f} m2 "
        f"({len(filtered)} patches; small-patch share "
        f"{(vector_total - kept) / vector_total:.1%})"
    )

    stats = LandscapeMetrics(config).calculate_area_statistics(polys)
    print("patch stats   : " + ", ".join(f"{k}={v:,.0f}" for k, v in stats.items()))


if __name__ == "__main__":
    main()
