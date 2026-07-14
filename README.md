![CI](https://github.com/ferg-dwr/eo-water-volume-estimator/actions/workflows/ci.yml/badge.svg?branch=dev)
# eo-water-volume-estimator

**How much water is in the Yolo Bypass right now?** This repository answers
that question using satellites — no field visit required.

It combines a NASA satellite map of *where* water is, the State of
California's own map of *how deep the ground is*, and a river gauge's reading
of *how high the water stands*, and integrates the three into an estimate of
**how much water** is present: a single number (in acre-feet), maps you can
open in any GIS, and — just as important — an honest per-pixel map of how
wrong the number could be.

Built at the California Department of Water Resources (DWR) as operational
decision-support tooling. No remote-sensing background is assumed below.

---

## The idea

Think of the Yolo Bypass as a giant, oddly-shaped bathtub.

To know how much water is in a bathtub you need three things: the **shape of
the tub** (how deep is the bottom at every point?), **where the water is**
(which parts of the tub are wet?), and the **height of the waterline**.
Multiply it out and you have a volume. That is the entire method:

$$V = A_{\text{pixel}} \times \sum_i f_i \times \max\left(w - b_i,\ 0\right)$$

where $V$ is volume (m³), $A_{\text{pixel}}$ the pixel area (900 m²), $f_i$
the water fraction of pixel $i$, $w$ the water-surface elevation, and $b_i$
the bed elevation (both m NAVD88).

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
**open water**, **inundated vegetation** (water among plants — counted as
water here, per the product specification), **not water**, or **masked** (the
sensor couldn't get an answer there: terrain shadow, layover, or outside the
imaged swath). New coverage arrives every 6–12 days, free.

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

## Ingredient 3 — the waterline (a real gauge, on the same ruler)

The water-surface elevation (WSE) now comes from a **river gauge**: CDEC
station LIS (Lisbon, in the Toe Drain), whose zero point is surveyed to the
same NAVD88 ruler as the DEM — verified, not assumed — and whose reading is
matched to the satellite's exact overpass minute. Swapping the earlier
shoreline-based estimate for the gauge moved the flagship number by **+34%**,
which is why this was milestone 3.

WSE estimation is **pluggable**: every method is a versioned model with a
documented ID (see [`MODELS.md`](MODELS.md)), its ID stamped into every
output filename and file's metadata. Four models ship today — the gauge
anchor (primary), the self-contained shoreline fallback, and two experimental
tilted-surface models that currently *fail their own accuracy self-check*
(shoreline pixels sample slightly submerged ground, a ~0.5 m bias) and say so
in their documentation rather than pretending otherwise.

## What one run produces

A summary (volume, wet area, mean/max depth, gauge diagnostics, and honesty
metrics) plus **four GeoTIFF maps**, each openable in QGIS/ArcGIS:

| product | meaning | no-data rule |
|---|---|---|
| `*_water_*.tif`  | water fraction the satellite saw | "sensor couldn't see" is no-data — never counted as dry |
| `*_depth_*.tif`  | water depth in meters, only where water was detected | "no water" is no-data — it is not "0 m of water" |
| `*_volume_*.tif` | cubic meters of water per pixel | summing its valid pixels reproduces the reported total exactly |
| `*_uncertainty_*.tif` | how wrong the volume could be, per pixel (m³) | valid on **all** AOI pixels — sensor-blind spots are where uncertainty lives |

Every file carries its provenance in metadata: source granule, DEM version,
which WSE model produced it, and the uncertainty budget. Filenames embed the
model ID, so runs under different methods never overwrite each other.

## The uncertainty budget (and the map that recommends sensors)

Every run decomposes "how wrong could this be" into labeled terms with
different standing: **exact bounds** (sensor-blind pixels could hide up to a
full water column; partial classes carry their assumption explicitly), a
**labeled scenario** (a flat waterline degrades with distance from the
anchoring gauge — rate stated, not measured), and a **measured spread**
(rerun the scene under a different WSE model and difference the maps). The
terms are *summed*, not statistically combined — a deliberate, conservative
envelope (63% of volume on the flagship scene, dominated by the
distance-from-gauge term).

The per-pixel version of that budget doubles as an **instrument-siting map**:
it glows exactly where lots of water sits far from any live gauge — for the
Yolo Bypass, the tidal south end — quantifying where a new sensor buys the
most certainty.

## Results

**Flagship scene — January 15, 2026: 104,870 acre-feet (129.4 million m³)**
over 81 km² of detected water, at a gauge-anchored WSE of 4.084 m NAVD88.
Cross-checked against the gauge network: Fremont Weir had stopped spilling
two days earlier, so the scene is a draining bypass in **recession** — which
independently explains the flooded vegetation the radar saw.

**A full wet season — 37 scenes, Jan 15 to Mar 15, 2026:** the storage
hydrograph shows the January recession draining 129 → 42 million m³, a
February plateau, then a second, larger pulse peaking **March 3–4 at
135–145 million m³** — on which date the Fremont gauge confirms the weir was
actively spilling. Both flood pulses are corroborated by an entirely
independent measurement system. One scene's swath missed the bypass; the
pipeline reports it as *volume 0, confidence 0, could-be-hiding up to 109
million m³* — "we couldn't see" is a quantified answer here, never "dry."

External validation to date: wet area reproduced to **0.000%** by an
independent vector pipeline (`spatio_hydrograph`), and flood regime confirmed
against Fremont Weir stage (`inundation`).

## Honest limitations (read before trusting a number)

1. **One flat waterline.** The gauge is exact at Lisbon; the real surface
   tilts over a 59 km system whose south end is tidal. Measured sensitivity:
   ~54 million m³ per meter of WSE error. The distance term of the
   uncertainty budget carries this; a second live gauge or SWOT altimetry
   (next milestone) retires it.
2. **Shoreline-based WSE has a known bias.** Wet pixels at the water's edge
   sit slightly *in* the water, so their ground elevations read ~0.5 m low.
   This is why the tilted-surface models remain experimental and the gauge
   remains primary.
3. **Narrow channels are invisible** below the ~3 ha / 200 m product floor,
   and DSWx classifications carry their own error the budget does not model.
4. **The uncertainty envelope is conservative by design** — a labeled sum of
   bounds and scenarios, not a confidence interval.

## Install & run

```bash
pip install -e ".[io,eo,dev,viz]"   # numpy core + rasterio + earthaccess + pytest + matplotlib
python -m pytest -q                 # 54 tests
```

To reproduce the Yolo results you need (a) a free
[NASA Earthdata login](https://urs.earthdata.nasa.gov/), (b) the CNRA DEM
v4.3 GeoTIFF on disk, and (c) an AOI polygon (GeoJSON). Then:

```bash
python examples/run_yolo_volume.py       # one scene, all four WSE models compared
python examples/run_yolo_timeseries.py   # the whole season -> incremental CSV
python examples/validate_spill.py        # cross-check the regime vs Fremont Weir
python examples/validate_wet_area.py     # cross-check wet area vs spatio_hydrograph
```

## How the code is organized
```
src/eo_water_volume/
volume.py       the math: volume, per-pixel maps, shoreline WSE (pure numpy)
sources.py      reading & aligning rasters; DSWx classes -> water fractions
wse.py          pluggable WSE models + the model registry (see MODELS.md)
gauges.py       datum-corrected river-gauge readings (CDEC; NAVD88 meters)
uncertainty.py  the per-pixel uncertainty terms and scene budget
hydrograph.py   time-series loading + storage-hydrograph plotting ([viz])
outputs.py      writing georeferenced GeoTIFFs with metadata
contract.py     the data-request format shared with dwr-eo-toolkit
tests/            54 tests, incl. exact analytical solutions (tilted planes,
paraboloid bowls) and every uncertainty bound checked by hand
examples/         the Yolo runs + two external-validation scripts
```

## Relationship to dwr-eo-toolkit

[`dwr-eo-toolkit`](https://github.com/ferg-dwr/dwr-eo-toolkit) is DWR's
satellite-data acquisition service. The two repos deliberately share **no
code** — only a data-request format (`contract.py`) that matches the toolkit's
download API, so either side can evolve independently.

## Status

<<<<<<< HEAD
**v0.3.0.** Working end to end with gauge-anchored WSE, a four-model
registry, per-pixel uncertainty, a season-scale batch runner, and two
independent external validations. Next: SWOT altimetry (validating the
water-surface *shape* a single gauge cannot), an exploration notebook, and a
software paper.
=======
Early development (v0.x). Working end to end; next milestones: gauge-based
water levels, quantitative validation against DWR's inundation tooling, SWOT
altimetry, and toolkit integration.
>>>>>>> f763ae122ab9a78ad49374a4f0b2dc8f5ade0371
