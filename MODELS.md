# WSE model registry

Every water-surface-elevation (WSE) estimator carries a `MODEL_ID`. The ID is
stamped into output filenames (`yolo_<product>_<model_id>_<granule>.tif`) and
GeoTIFF provenance tags (`wse_model_id`), so any product on disk names the
method that made it. The machine-readable registry is `MODEL_REGISTRY` in
`src/eo_water_volume/wse.py`; this file is the human-readable companion.

**Versioning rule: any behavior change is a new ID** (`wse-perimeter-v1` ->
`wse-perimeter-v2`). Old outputs stay interpretable forever; nothing is ever
re-defined in place.

## Models

| MODEL_ID | class | WSE shape | needs | key assumptions | diagnostics reported | known failure modes |
|---|---|---|---|---|---|---|
| `wse-perimeter-v1` | `PerimeterWse` | flat (scalar) | nothing (self-contained) | mask edge is a real shoreline; single quasi-static pool | `wse_perimeter_m` | degrades where the edge is clouds, AOI cuts, or levee faces; measured +0.52-0.59 m low vs the LIS gauge on the 2026-01-15 Yolo scene |
| `wse-gauge-v1` | `GaugeWse` | flat (scalar) | one datum-corrected `GaugeReading` (NAVD88 m) within 3 h of sensing | flat pool across the whole AOI; gauge datum verified (see `gauges.py` header) | `wse_perimeter_m`, `wse_gauge_minus_perimeter_m`, `gauge_station`, `gauge_time_utc` | one point stretched over a 59 km system; tilt error grows with distance from the gauge (the uncertainty product's distance term bounds this as a labeled scenario) |

Reserved (designs in ROADMAP M3, not yet implemented): `wse-profile-v1`
(shoreline-sampled tilt anchored through a gauge), `wse-2gauge-v1` (linear
tilt between two live anchors -- blocked on a live southern gauge).

## Adding a model

1. Subclass `WseEstimator` in `src/eo_water_volume/wse.py`; set a new
   `MODEL_ID` (`wse-<family>-v1`).
2. `estimate()` returns a `WseField`: scalar for a flat surface or a
   per-pixel grid for a tilted one (the volume core broadcasts either), a
   `method` string that names itself, and `diagnostics` with the honesty
   numbers a reviewer would want.
3. Register it: add the entry to `MODEL_REGISTRY` and a row to the table
   above. A consistency test fails CI if a concrete estimator is missing
   from the registry.
4. Tests: pin the estimator's math against a case with a known answer
   (see the tilted-plane and paraboloid tests), plus its failure mode.
5. If the estimator replaces an assumption in the uncertainty budget
   (e.g. a measured slope replacing the distance SCENARIO), update the
   example's term wiring and say so in the commit message.