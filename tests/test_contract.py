"""The contract is the decoupling seam, so pin its shape with tests."""

from datetime import date

import pytest

from eo_water_volume.contract import (
    AOI,
    PRODUCT_SHORT_NAMES,
    DataRequest,
    requests_for_volume,
)


def _aoi():
    return AOI(-121.75, 38.20, -121.60, 38.35)


def test_toolkit_payload_shape():
    req = DataRequest(
        product="OPERA_DSWX_S1",
        aoi=_aoi(),
        start_date=date(2026, 2, 1),
        end_date=date(2026, 2, 15),
        output_dir="./downloads/run",
    )
    payload = req.to_toolkit_payload()
    # Exactly the keys the toolkit's POST /api/v1/downloads consumes.
    assert set(payload) == {
        "product",
        "min_lon",
        "min_lat",
        "max_lon",
        "max_lat",
        "start_date",
        "end_date",
        "max_results",
        "output_dir",
    }
    assert payload["start_date"] == "2026-02-01"
    assert payload["min_lon"] == -121.75


def test_short_name_resolution():
    req = DataRequest("OPERA_DSWX_S1", _aoi(), date(2026, 1, 1), date(2026, 1, 2))
    assert req.short_name() == "OPERA_L3_DSWX-S1_V1"
    unmapped = DataRequest("UNKNOWN", _aoi(), date(2026, 1, 1), date(2026, 1, 2))
    assert unmapped.short_name() is None


def test_swot_collections_split_by_version():
    # Version C ends 2025-05-03; Version D begins 2025-05-05 (PO.DAAC).
    assert PRODUCT_SHORT_NAMES["SWOT_HR_RASTER"] == "SWOT_L2_HR_Raster_D"
    assert PRODUCT_SHORT_NAMES["SWOT_HR_RASTER_VC"] == "SWOT_L2_HR_Raster_2.0"


def test_requests_for_volume_asks_for_the_mask():
    reqs = requests_for_volume(_aoi(), date(2026, 2, 1), date(2026, 2, 15))
    assert len(reqs) == 1
    assert reqs[0].product == "OPERA_DSWX_S1"
    assert reqs[0].short_name() == "OPERA_L3_DSWX-S1_V1"


def test_degenerate_bbox_rejected():
    with pytest.raises(ValueError):
        AOI(-121.60, 38.20, -121.75, 38.35)  # min_lon > max_lon


def test_inverted_dates_rejected():
    with pytest.raises(ValueError):
        DataRequest("OPERA_DSWX_S1", _aoi(), date(2026, 2, 15), date(2026, 2, 1))


def test_aoi_bbox_order_matches_earthaccess():
    assert _aoi().as_bbox() == (-121.75, 38.20, -121.60, 38.35)
