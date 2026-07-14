"""First real run: Yolo Bypass water volume from OPERA DSWx-S1 + CNRA DEM v4.3.

Two stages, deliberately separable:
  fetch()   -- needs the network + an Earthdata Login (earthaccess, [eo] extra)
  analyze() -- pure local: mask + DEM + AOI polygon -> summary + volume GeoTIFF

Run from the repo root:  python examples/run_yolo_volume.py
First run downloads one granule to DOWNLOAD_DIR; later runs reuse it.

The AOI polygon (not just its bbox) clips the analysis: the bbox is used only
for the data search, so river water outside the bypass never pollutes the
volume. Every registered WSE model runs on the scene -- perimeter always;
gauge, shoreline-profile v1, and v2 when CDEC answers -- printing a method
comparison. The gauge-anchored flat pool remains the PRIMARY result (the
profile models are experimental pending their self-check gate); perimeter is
the loud, labeled fallback when the gauge is unreachable.
"""

from __future__ import annotations

import glob
import json
from datetime import date
from pathlib import Path

import numpy as np

from eo_water_volume.contract import AOI, requests_for_volume
from eo_water_volume.gauges import CdecStation
from eo_water_volume.outputs import write_geotiff
from eo_water_volume.sources import LocalFileSource
from eo_water_volume.uncertainty import (
    budget,
    combine,
    invalid_term,
    partial_fraction_term,
    wse_distance_term,
)
from eo_water_volume.volume import perimeter_invalid_fraction, summarize, volume_map
from eo_water_volume.wse import (
    GaugeWse,
    PerimeterWse,
    ShorelineProfileWse,
    ShorelineProfileWseV2,
    WseEstimator,
)

# --- config -----------------------------------------------------------------
REPO = Path(__file__).resolve().parents[1]
GEOJSON_PATH = REPO / "data/polys/yolo-bypass-boundary.geojson"
DEM_PATH = REPO / "data/dem/CNRA/dem_delta_10m_20250312/dem_delta_10m_20250312.tif"
DOWNLOAD_DIR = REPO / "data/downloads/yolo"
OUTPUT_DIR = REPO / "data/outputs"
LIS_LON, LIS_LAT = -121.588, 38.475  # CDEC LIS (Lisbon), the WSE anchor
SLOPE_RATE = 5e-5  # m/m -- SCENARIO knob for the WSE-distance term
TEMPORAL = (date(2026, 1, 15), date(2026, 3, 15))  # 2026 wet season; safely
# past the DSWx-S1 S1A/S1C mixing anomaly (granules dated 2025-05-20..12-09).
# ------------------------------------------------------------------------------


def load_aoi() -> tuple[dict, AOI]:
    """Read the AOI polygon; return (geometry, bbox) -- bbox for search only."""
    gj = json.loads(GEOJSON_PATH.read_text())
    feats = gj["features"] if gj["type"] == "FeatureCollection" else [gj]
    geom = feats[0]["geometry"]

    xs: list[float] = []
    ys: list[float] = []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for e in c:
                walk(e)

    walk(geom["coordinates"])
    return geom, AOI(min(xs), min(ys), max(xs), max(ys))


def gauge_row_for(wtr_path: Path, lon: float, lat: float) -> int:
    """Image row of a lon/lat point in this granule (for profile anchoring)."""
    import rasterio
    from rasterio.warp import transform as warp_transform

    with rasterio.open(wtr_path) as src:
        (x,), (y,) = warp_transform("EPSG:4326", src.crs, [lon], [lat])
        row, _ = src.index(x, y)
    return int(row)


def overpass_time_utc(wtr_path: Path):
    """Acquisition instant from the OPERA granule filename.

    DSWx-S1 filenames carry two timestamps; the FIRST is the sensing time
    (e.g. ..._T10SFH_20260115T015843Z_<processing>Z_...). Returns aware UTC.
    """
    import re
    from datetime import datetime, timezone

    m = re.search(r"_(\d{8}T\d{6})Z_", wtr_path.name)
    if not m:
        raise ValueError(f"No sensing timestamp in granule name: {wtr_path.name}")
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def fetch(aoi: AOI) -> Path:
    """Download one DSWx-S1 granule intersecting the AOI; return the WTR path."""
    existing = sorted(glob.glob(str(DOWNLOAD_DIR / "*B01_WTR*.tif")))
    if existing:
        print(f"reusing downloaded granule: {Path(existing[0]).name}")
        return Path(existing[0])

    import earthaccess  # [eo] extra; not needed for analyze()

    need = requests_for_volume(aoi, *TEMPORAL)[0]
    earthaccess.login()  # ~/.netrc or interactive prompt
    results = earthaccess.search_data(
        short_name=need.short_name(),
        bounding_box=need.aoi.as_bbox(),
        temporal=(need.start_date.isoformat(), need.end_date.isoformat()),
    )
    if not results:
        raise SystemExit("No DSWx-S1 granules found; widen TEMPORAL.")
    print(f"{len(results)} granule(s) found; downloading the first.")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    earthaccess.download(results[:1], local_path=str(DOWNLOAD_DIR))

    hits = sorted(glob.glob(str(DOWNLOAD_DIR / "*B01_WTR*.tif")))
    if not hits:
        raise SystemExit("Download finished but no B01_WTR layer found.")
    return Path(hits[0])


def analyze(
    wtr_path: Path,
    dem_path: Path,
    aoi_geom: dict,
    out_dir: Path,
    estimator: WseEstimator | None = None,
) -> dict:
    """Volume, depth, water-fraction, and uncertainty products within the AOI.

    Four single-band GeoTIFFs with NODATA=-9999 declared in metadata:
    *_volume      : m^3 per pixel; 0.0 is a real value (analyzed, no water);
                    nodata outside the AOI and where the sensor gave no answer
    *_depth       : water-column depth (wse - bed, m) only where water was
                    detected; nodata elsewhere -- "no water" is not "0 m of water"
    *_water       : DSWx-derived water fraction; nodata outside the AOI and
                    at invalid (HAND/layover/shadow) pixels
    *_uncertainty : combined per-pixel volume-uncertainty envelope (m^3);
                    valid on ALL AOI pixels incl. sensor-blind ones -- the
                    blind pixels are where uncertainty lives; nodata only
                    outside the polygon
    """
    from rasterio.features import geometry_mask
    from rasterio.warp import transform_geom

    src = LocalFileSource(mask_path=str(wtr_path), dem_path=str(dem_path))
    mask_raster = src.water_mask()
    water, bed, pixel_area, invalid = src.load_aligned()

    # AOI polygon (WGS84 per RFC 7946) -> mask grid CRS -> boolean inside-mask.
    geom_utm = transform_geom("EPSG:4326", mask_raster.crs, aoi_geom)
    inside = geometry_mask(
        [geom_utm],
        out_shape=water.shape,
        transform=mask_raster.transform,
        invert=True,  # True = pixel inside the polygon
    )
    if not inside.any():
        raise SystemExit("AOI polygon does not intersect this granule.")

    water_aoi = np.where(inside, water, 0.0)

    # The WSE method is a swappable estimator; PerimeterWse is the
    # self-contained default. Every estimator names itself and carries its
    # diagnostics (e.g. the gauge-perimeter gap) into stats and provenance.
    est = estimator or PerimeterWse()
    wse_field = est.estimate(
        bed, water_aoi, when_utc=overpass_time_utc(wtr_path), region=inside
    )

    wse = wse_field.values
    wse_method = wse_field.method
    stats = summarize(bed, water_aoi, wse, pixel_area, invalid=(invalid & inside))
    # Honest denominators: fraction of *AOI* pixels the sensor couldn't see,
    # not fraction of the whole scene (which dilutes the metric).
    stats["invalid_fraction_aoi"] = float(invalid[inside].mean())
    stats["aoi_pixel_share_of_scene"] = float(inside.mean())
    stats["wse_model_id"] = est.MODEL_ID
    stats["wse_perimeter_invalid_fraction"] = perimeter_invalid_fraction(
        water_aoi, invalid & inside
    )
    stats.update(wse_field.summary_stats())
    stats.update(wse_field.diagnostics)

    # --- uncertainty terms (see uncertainty.py for the epistemics) ----------
    # Along-axis (northing) distance from the anchoring gauge, per pixel row.
    from rasterio.warp import transform as warp_transform

    _, (gauge_northing,) = warp_transform(
        "EPSG:4326", mask_raster.crs, [LIS_LON], [LIS_LAT]
    )
    t = mask_raster.transform
    row_northing = t.f + (np.arange(water.shape[0]) + 0.5) * t.e
    dist_m = np.broadcast_to(
        np.abs(row_northing - gauge_northing)[:, None], water.shape
    )

    partial = (mask_raster.data == 2) & inside  # DSWx class 2 = partial water
    unc_partial = partial_fraction_term(bed, wse, pixel_area, partial)
    unc_invalid = invalid_term(bed, wse, pixel_area, invalid & inside)
    unc_wse = wse_distance_term(water_aoi, pixel_area, dist_m, SLOPE_RATE)

    # Measured method-spread: |volume difference| vs the perimeter method
    # (zero by construction when the perimeter method IS the estimator).
    vmap = volume_map(bed, water_aoi, wse, pixel_area)
    if est.MODEL_ID == PerimeterWse.MODEL_ID:
        unc_spread = np.zeros_like(vmap)
    else:
        perim_field = PerimeterWse().estimate(bed, water_aoi)
        unc_spread = np.abs(
            vmap - volume_map(bed, water_aoi, perim_field.values, pixel_area)
        )

    unc = combine(unc_partial, unc_invalid, unc_wse, unc_spread)
    stats.update(
        budget(
            {
                "partial": unc_partial,
                "invalid": unc_invalid,
                "wse_distance_scenario": unc_wse,
                "method_spread": unc_spread,
            },
            stats["volume_m3"],
        )
    )

    # --- four products, shared nodata semantics ------------------------------
    NODATA = -9999.0
    answered = inside & ~invalid  # sensor gave an answer, in the AOI

    vmap = np.where(answered, vmap, NODATA)

    depth = np.clip(wse - bed, 0.0, None)
    wet = answered & (water_aoi > 0) & np.isfinite(bed)
    depth = np.where(wet, depth, NODATA)  # depth only where water was detected

    frac = np.where(answered, water_aoi, NODATA)
    unc = np.where(inside, unc, NODATA)  # defined on ALL AOI pixels (invalid
    # pixels carry their own bound), nodata only outside the polygon

    out_dir.mkdir(parents=True, exist_ok=True)
    common_tags = {
        "aoi": "yolo-bypass-boundary",
        "wse_m_navd88": stats["wse_m"],
        "wse_method": wse_method,
        "wse_model_id": est.MODEL_ID,
        "wse_perimeter_m": stats["wse_perimeter_m"],
        "invalid_fraction_aoi": stats["invalid_fraction_aoi"],
        "unc_total_m3": stats["unc_total_m3"],
        "unc_slope_rate_scenario_m_per_m": SLOPE_RATE,
        "source_granule": wtr_path.name,
        "dem": dem_path.name,
    }
    common_tags = {k: v for k, v in common_tags.items() if v is not None}
    products = {
        "volume": (vmap, "water_volume_m3", "m^3"),
        "depth": (depth, "water_depth_m", "m"),
        "water": (frac, "water_fraction", "fraction"),
        "uncertainty": (unc, "volume_uncertainty_m3", "m^3"),
    }

    for key, (grid, band, unit) in products.items():
        out_tif = out_dir / f"yolo_{key}_{est.MODEL_ID}_{wtr_path.stem}.tif"
        write_geotiff(
            grid,
            mask_raster,
            str(out_tif),
            nodata=NODATA,
            band_name=band,
            units=unit,
            tags=common_tags,
        )
        stats[f"{key}_map"] = str(out_tif)
    return stats


def main() -> None:
    aoi_geom, aoi_bbox = load_aoi()
    print(f"AOI bbox (search only): {aoi_bbox.as_bbox()}")
    wtr_path = fetch(aoi_bbox)

    # Build the estimator lineup. Only the CDEC call can fail; everything
    # else is local. Gauge-dependent models drop out loudly if it does.
    estimators: list[WseEstimator] = [PerimeterWse()]
    try:
        reading = CdecStation().wse_navd88_m(overpass_time_utc(wtr_path))
        g_row = gauge_row_for(wtr_path, LIS_LON, LIS_LAT)
        estimators.append(GaugeWse(reading))
        estimators.append(ShorelineProfileWse(anchor=reading, gauge_row=g_row))
        estimators.append(ShorelineProfileWseV2(anchor=reading, gauge_row=g_row))
    except (OSError, LookupError) as err:
        print(f"WARNING: gauge unavailable ({err}); running perimeter model only")

    results = {}
    for est in estimators:
        results[est.MODEL_ID] = analyze(
            wtr_path, DEM_PATH, aoi_geom, OUTPUT_DIR, estimator=est
        )

    print("\n--- method comparison ---")
    print(f"{'model':<18s} {'volume_m3':>15s} {'acre_ft':>12s} {'wse_m':>7s}")
    for mid, st in results.items():
        print(
            f"{mid:<18s} {st['volume_m3']:>15,.0f} "
            f"{st['volume_acre_ft']:>12,.0f} {st['wse_m']:>7.3f}"
        )

    primary = results.get("wse-gauge-v1") or next(iter(results.values()))
    print("\n--- primary run (gauge-anchored; profile is experimental) ---")
    for k, v in primary.items():
        print(f"  {k:32s} {v}")


if __name__ == "__main__":
    main()
