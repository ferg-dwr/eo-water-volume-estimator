"""First real run: Yolo Bypass water volume from OPERA DSWx-S1 + CNRA DEM v4.3.

Two stages, deliberately separable:
  fetch()   -- needs the network + an Earthdata Login (earthaccess, [eo] extra)
  analyze() -- pure local: mask + DEM + AOI polygon -> summary + volume GeoTIFF

Run from the repo root:  python examples/run_yolo_volume.py
First run downloads one granule to DOWNLOAD_DIR; later runs reuse it.

The AOI polygon (not just its bbox) clips the analysis: the bbox is used only
for the data search, so river water outside the bypass never pollutes the
volume. WSE comes from the mask shoreline (perimeter median) until a gauge
stage replaces it in M3 -- where the polygon edge cuts through water, that
estimate degrades; treat the first number accordingly.
"""

from __future__ import annotations

import glob
import json
from datetime import date
from pathlib import Path

import numpy as np

from eo_water_volume.contract import AOI, requests_for_volume
from eo_water_volume.outputs import write_geotiff
from eo_water_volume.sources import LocalFileSource
from eo_water_volume.volume import summarize, volume_map, wse_from_perimeter

# --- config -----------------------------------------------------------------
REPO = Path(__file__).resolve().parents[1]
GEOJSON_PATH = REPO / "data/polys/yolo-bypass-boundary.geojson"
DEM_PATH = REPO / "data/dem/CNRA/dem_delta_10m_20250312/dem_delta_10m_20250312.tif"
DOWNLOAD_DIR = REPO / "data/downloads/yolo"
OUTPUT_DIR = REPO / "data/outputs"
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


def analyze(wtr_path: Path, dem_path: Path, aoi_geom: dict, out_dir: Path) -> dict:
    """Volume within the AOI polygon; writes a georeferenced volume map."""
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
    coverage = inside.mean()

    wse = wse_from_perimeter(bed, water_aoi)
    stats = summarize(bed, water_aoi, wse, pixel_area, invalid=(invalid & inside))
    # Honest denominators: fraction of *AOI* pixels the sensor couldn't see,
    # not fraction of the whole scene (which dilutes the metric).
    stats["invalid_fraction_aoi"] = float(invalid[inside].mean())
    stats["aoi_pixel_share_of_scene"] = float(coverage)

    vmap = volume_map(bed, water_aoi, wse, pixel_area)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_tif = out_dir / f"yolo_volume_{wtr_path.stem}.tif"
    write_geotiff(
        vmap,
        mask_raster,
        str(out_tif),
        band_name="water_volume_m3",
        units="m^3",
        tags={
            "aoi": "yolo-bypass-boundary",
            "wse_m_navd88": stats["wse_m"],
            "wse_method": "mask-perimeter median (gauge stage pending, M3)",
            "invalid_fraction_aoi": stats["invalid_fraction_aoi"],
            "source_granule": wtr_path.name,
            "dem": dem_path.name,
        },
    )
    stats["volume_map"] = str(out_tif)
    return stats


def main() -> None:
    aoi_geom, aoi_bbox = load_aoi()
    print(f"AOI bbox (search only): {aoi_bbox.as_bbox()}")
    wtr_path = fetch(aoi_bbox)
    stats = analyze(wtr_path, DEM_PATH, aoi_geom, OUTPUT_DIR)
    print("\n--- Yolo Bypass volume ---")
    for k, v in stats.items():
        print(f"  {k:24s} {v}")


if __name__ == "__main__":
    main()
