# eo-water-volume-estimator

**How much water is in the Yolo Bypass right now?** This repository answers
that question using satellites — no field visit required.

It combines a NASA satellite map of *where* water is with the State of
California's own map of *how deep the ground is*, and integrates the two into
an estimate of **how much water** is present: a single number (in acre-feet)
plus maps you can open in any GIS.

Built at the California Department of Water Resources (DWR) as operational
decision-support tooling. No remote-sensing background is assumed below.

---

## The idea, in plain language

Think of the Yolo Bypass as a giant, oddly-shaped bathtub.

To know how much water is in a bathtub you need three things: the **shape of
the tub** (how deep is the bottom at every point?), **where the water is**
(which parts of the tub are wet?), and the **height of the waterline**.
Multiply it out and you have a volume. That is the entire method:

```
volume = pixel_area × Σ water_fraction × max(water_surface − bed_elevation, 0)
```

Reading that term by term:

- The landscape is divided into **pixels** — 30 m × 30 m squares (900 m² each).
- **water_fraction** — how much of each pixel is wet (0 = dry, 1 = fully wet),
  from a satellite.
- **bed_elevation** — the height of the ground (or river/lake bottom) in that
  pixel, from an elevation model.
- **water_surface** — the elevation of the water's top. Surface minus bed is
  depth; the `max(…, 0)` just says dry ground holds no water.
- Sum over all pixels, multiply by pixel area: volume in cubic meters
  (reported in acre-feet too, the unit California water managers use).

## Ingredient 1 — where the water is (NASA OPERA DSWx-S1)

Most satellite cameras can't see through clouds — a problem in a Delta winter.
So we use a **radar** satellite (Sentinel-1): radar supplies its own
illumination and passes through clouds and darkness. Calm water reflects the
radar pulse away like a mirror, so water shows up distinctly.

We do **not** classify the radar imagery ourselves. NASA already publishes a
validated, analysis-ready water map called
[OPERA DSWx-S1](https://podaac.jpl.nasa.gov/dataset/OPERA_L3_DSWX-S1_V1)
("Dynamic Surface Water eXtent from Sentinel-1"): every 30 m pixel is labeled
**open water**, **partial water** (e.g. flooded vegetation), **not water**, or
**invalid** (the sensor couldn't get an answer there). New coverage arrives
every 6–12 days, free.

One built-in limit: the product maps open water bodies larger than ~3 hectares
and ~200 m across — broad floodplains show up well; narrow channels don't.

## Ingredient 2 — how deep the ground is (CNRA Bay-Delta DEM)

A **DEM** (digital elevation model) is a grid of ground heights. DWR and USGS
maintain a seamless 10 m DEM of the whole Delta —
[SF Bay & Sacramento–San Joaquin Delta DEM for Modeling, v4.3](https://data.cnra.ca.gov/dataset/san-francisco-bay-and-sacramento-san-joaquin-delta-dem-for-modeling-version-4-3)
— that fuses boat-based **bathymetry** (underwater depth soundings) with
airborne lidar for dry land. Elevations are in meters above **NAVD88**, the
standard "zero point" US agencies use so different datasets agree on what
"elevation 0 m" means.

This dataset is the quiet superpower of the project: for the Delta, the "how
deep is the tub" half of the problem is already solved and maintained by the
State.

## Ingredient 3 — the waterline

The one number neither dataset provides directly is the **water-surface
elevation (WSE)**. For now we estimate it from the water's edge: depth is zero
exactly at a shoreline, so the ground elevation along the satellite-detected
water boundary approximates the water surface. It's self-contained and needs
no field data — and it is also the largest error source (see limitations).
Replacing it with real river-gauge readings is the next milestone.

## What one run produces

A summary (volume, wet area, mean/max depth, and an honesty metric — the
fraction of pixels the sensor couldn't classify) plus **three GeoTIFF maps**,
each openable in QGIS/ArcGIS:

| product | meaning | no-data rule |
|---|---|---|
| `*_water_*.tif`  | water fraction the satellite saw (0 / 0.5 / 1) | "sensor couldn't see" is no-data — never counted as dry |
| `*_depth_*.tif`  | water depth in meters, only where water was detected | "no water" is no-data — it is not "0 m of water" |
| `*_volume_*.tif` | cubic meters of water per pixel | summing its valid pixels reproduces the reported total exactly |

Every file carries its provenance in metadata: source granule, DEM version,
how the WSE was estimated, and the invalid-pixel fraction.

## First real result

**January 15, 2026 — 76,249 acre-feet (94.1 million m³) of water in the Yolo
Bypass**, over a detected wet area of 77 km². The spatial pattern matches the
bypass's known hydrology — water in the perennial Tule Canal / Toe Drain along
the east side and the tidal south end — i.e. a bypass between flood pulses.

## Honest limitations (read before trusting a number)

1. **One flat waterline.** We assume a single WSE across a 59 km system whose
   south end is tidal. Sensitivity is quantified: each 0.5 m of WSE error
   moves the estimate by ~38 million m³ (~±40%). Gauge-based WSE is the fix,
   and it's next.
2. **"Partial water" is a stated guess.** The satellite class carries no
   percentage; we count those pixels as half wet (`PARTIAL_WATER_FRACTION =
   0.5`) — a documented, tunable heuristic, not a measurement.
3. **Narrow channels are invisible** below the ~3 ha / 200 m product floor.
4. **Pattern-validated only, so far.** The map agrees with known hydrology,
   but no independent quantitative validation has been run yet. That is the
   next milestone, not a footnote.

## Install & run

```bash
pip install -e ".[io,eo,dev]"   # numpy core + rasterio + earthaccess + pytest
python -m pytest -q             # 24 tests
```

To reproduce the Yolo run you need (a) a free
[NASA Earthdata login](https://urs.earthdata.nasa.gov/), (b) the CNRA DEM
v4.3 GeoTIFF on disk, and (c) an AOI polygon (GeoJSON). Then:

```bash
python examples/run_yolo_volume.py
```

The first run downloads one satellite granule (cached afterward) and writes
the summary plus the three GeoTIFFs to `data/outputs/`.

## How the code is organized
```
src/eo_water_volume/
volume.py     the math: volume, per-pixel volume map, shoreline WSE (pure numpy)
sources.py    reading & aligning rasters; satellite classes -> water fractions
outputs.py    writing georeferenced GeoTIFFs with metadata
contract.py   the data-request format shared with dwr-eo-toolkit
tests/          24 tests, incl. validation against an exact analytical solution
examples/       the end-to-end Yolo Bypass run
```

The volume math is verified against a paraboloid "bowl" with a known
closed-form volume (agreement < 1%).

## Relationship to dwr-eo-toolkit

[`dwr-eo-toolkit`](https://github.com/ferg-dwr/dwr-eo-toolkit) is DWR's
satellite-data acquisition service. The two repos deliberately share **no
code** — only a data-request format (`contract.py`) that matches the toolkit's
download API, so either side can evolve independently.

## Status

Early development (v0.x). Working end to end; next milestones: gauge-based
water levels, quantitative validation against DWR's inundation tooling, SWOT
altimetry, and toolkit integration.