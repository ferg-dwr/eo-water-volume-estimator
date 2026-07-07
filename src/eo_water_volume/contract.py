"""Data contract between the volume model and whatever fetches its data.

The model declares *what it needs* as `DataRequest` objects shaped like the
dwr-eo-toolkit `POST /api/v1/downloads` payload. The toolkit -- or any other
fetcher, or a human with a curl command -- fulfils them. Neither package
imports the other; the only shared thing is the JSON shape of a request.

Stdlib only: zero dependencies, importable anywhere.

Short names (CMR collection identifiers a fetcher resolves products to):
  - OPERA_L3_DSWX-S1_V1 / OPERA_L3_DSWX-HLS_V1 (OPERA DSWx product specs)
  - SWOT raster: the collection is VERSIONED BY TIME RANGE. Version C
    (SWOT_L2_HR_Raster_2.0) measurements end 2025-05-03; Version D
    (SWOT_L2_HR_Raster_D) measurements begin 2025-05-05 (PO.DAAC cookbook /
    Version C-to-D transition). Requests for current data must target _D.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Logical product name (what the model asks for) -> CMR short_name (what a
# fetcher resolves it to). Wiring an OPERA adapter into dwr-eo-toolkit is
# toolkit-side work; this mapping documents the target collections.
PRODUCT_SHORT_NAMES: dict[str, str] = {
    "OPERA_DSWX_S1": "OPERA_L3_DSWX-S1_V1",
    "OPERA_DSWX_HLS": "OPERA_L3_DSWX-HLS_V1",
    "SWOT_HR_RASTER": "SWOT_L2_HR_Raster_D",          # current (>= 2025-05-05)
    "SWOT_HR_RASTER_VC": "SWOT_L2_HR_Raster_2.0",     # archive (<= 2025-05-03)
}


@dataclass(frozen=True)
class AOI:
    """Bounding box in WGS84 lon/lat."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def __post_init__(self) -> None:
        if not (self.min_lon < self.max_lon and self.min_lat < self.max_lat):
            raise ValueError(f"Degenerate bbox: {self}")

    def as_bbox(self) -> tuple[float, float, float, float]:
        """(min_lon, min_lat, max_lon, max_lat) -- the earthaccess bbox order."""
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


@dataclass(frozen=True)
class DataRequest:
    """One dataset need, in the dwr-eo-toolkit download-request shape."""

    product: str                 # logical name; see PRODUCT_SHORT_NAMES
    aoi: AOI
    start_date: date
    end_date: date
    max_results: int = 50
    output_dir: str | None = None

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise ValueError(
                f"end_date {self.end_date} precedes start_date {self.start_date}"
            )

    def short_name(self) -> str | None:
        """CMR short_name for this product, or None if unmapped."""
        return PRODUCT_SHORT_NAMES.get(self.product)

    def to_toolkit_payload(self) -> dict:
        """The JSON body dwr-eo-toolkit's POST /api/v1/downloads consumes."""
        return {
            "product": self.product,
            "min_lon": self.aoi.min_lon,
            "min_lat": self.aoi.min_lat,
            "max_lon": self.aoi.max_lon,
            "max_lat": self.aoi.max_lat,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "max_results": self.max_results,
            "output_dir": self.output_dir,
        }


def requests_for_volume(
    aoi: AOI,
    start_date: date,
    end_date: date,
    output_dir: str | None = None,
    max_results: int = 50,
) -> list[DataRequest]:
    """The datasets the volume model needs for an AOI + time window.

    Today: just the OPERA DSWx-S1 water mask. The bathymetric DEM is a static
    local input (CNRA portal download), not fetched per run. SWOT WSE /
    water_fraction joins this list in M4.
    """
    return [
        DataRequest(
            product="OPERA_DSWX_S1",
            aoi=aoi,
            start_date=start_date,
            end_date=end_date,
            max_results=max_results,
            output_dir=output_dir,
        )
    ]