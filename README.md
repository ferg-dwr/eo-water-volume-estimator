# eo-water-volume-estimator

Estimate surface-water **volume** for an area of the California Delta from an
Earth-observation water mask and a bathymetric DEM. Built as operational
decision-support tooling at the California Department of Water Resources (DWR);
decoupled from [`dwr-eo-toolkit`](https://github.com/ferg-dwr/dwr-eo-toolkit),
which handles data acquisition.

The whole model is one masked reduction — the volume of water under a planar
surface at elevation `wse`, integrated against the bed:

    V = pixel_area * Σ ( water_i × max(wse − bed_i, 0) )

where `water` is a per-pixel coverage fraction in [0, 1] and `bed` is the DEM.

## Data sources

- **Water mask:** [OPERA DSWx-S1](https://podaac.jpl.nasa.gov/dataset/OPERA_L3_DSWX-S1_V1)
  (Dynamic Surface Water Extent from Sentinel-1, Version 1). All-weather SAR
  water classification at 30 m on the MGRS grid, 6–12 day revisit; maps open
  water bodies larger than 3 ha and 200 m wide. Forward production since
  Sept 2024. Product spec: [JPL OPERA DSWx suite](https://www.jpl.nasa.gov/go/opera/products/dswx-product-suite/).
  The optical sibling, [DSWx-HLS](https://podaac.jpl.nasa.gov/dataset/OPERA_L3_DSWX-HLS_V1),
  reaches back to April 2023 for clear-sky work.
- **Bed elevation:** [San Francisco Bay and Sacramento–San Joaquin Delta DEM
  for Modeling, v4.3](https://data.cnra.ca.gov/dataset/san-francisco-bay-and-sacramento-san-joaquin-delta-dem-for-modeling-version-4-3)
  (CNRA open-data portal; v4.2 is superseded but archived). Seamless
  bathymetric + topographic elevation, NAVD88.

## Install

```bash
pip install -e .            # core: pure numpy
pip install -e ".[io]"      # + rasterio (reading/aligning GeoTIFFs)
pip install -e ".[dev]"     # + pytest
```

## Quickstart

With a DSWx `B01_WTR` GeoTIFF and the DEM on disk:

```python
from eo_water_volume import estimate_volume, wse_from_perimeter, summarize
from eo_water_volume.sources import LocalFileSource

src = LocalFileSource(mask_path="B01_WTR.tif", dem_path="dem_bay_delta_10m.tif")
water, bed, pixel_area = src.load_aligned()   # DEM reprojected onto the mask grid

wse = wse_from_perimeter(bed, water)          # WSE from the mask shoreline
print(summarize(bed, water, wse, pixel_area)) # volume in m^3 and acre-feet
```

Swap `wse_from_perimeter(...)` for a gauge stage (same NAVD88 datum) whenever
one is available — the perimeter estimate is the self-contained fallback.

## Assumptions and honest caveats

- **Planar pool.** One scalar water-surface elevation per AOI. Reasonable for a
  quasi-static floodplain or managed-inundation area; wrong for tidal channel
  networks, where WSE varies in space and sub-daily.
- **Partial water is a heuristic.** DSWx-S1's partial-surface-water class is
  categorical — it carries no measured sub-pixel fraction. `PARTIAL_WATER_FRACTION`
  (default 0.5) is an explicit, tunable knob, not a measurement.
- **Invalid pixels currently count as dry.** Cloud/layover/shadow-masked pixels
  (codes ≥ 252) contribute zero volume, silently. Until per-run invalid-fraction
  reporting lands, treat absolute numbers over partially masked scenes with care.
- **Mask floor.** DSWx-S1 resolves open water ≥ 3 ha and ≥ 200 m wide; narrow
  Delta channels will not appear.

## Relationship to dwr-eo-toolkit

The model declares its data needs via `eo_water_volume.contract` —
`requests_for_volume(...)` emits request objects whose
`to_toolkit_payload()` is exactly the JSON body of the toolkit's
`POST /api/v1/downloads`. Neither package imports the other; the JSON shape is
the entire coupling. Any fetcher (the toolkit, `earthaccess` directly, or a
curl command) can fulfil a request.

## Tests

```bash
python -m pytest -q
```

The volume core is validated against a paraboloid with a closed-form analytical
volume (agreement < 1%); the contract tests pin the toolkit payload shape.

## Status

Early development. Current scope: single-granule, single-pool volume from
OPERA DSWx-S1 + the CNRA DEM. Gauge-stage WSE, SWOT `water_fraction`
integration, and the toolkit adapter are planned follow-ons.