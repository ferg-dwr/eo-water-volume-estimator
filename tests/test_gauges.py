"""Tests for the gauge WSE layer -- all offline, against the verified CDEC format."""

from datetime import datetime, timezone

import pytest

from eo_water_volume.gauges import (
    M_PER_FT,
    CdecStation,
    GaugeSource,
    parse_cdec_csv,
)

# Format verified against a live CSVDataServlet response, 2026-07-08.
CANNED = """STATION_ID,DURATION,SENSOR_NUMBER,SENSOR_TYPE,DATE TIME,OBS DATE,VALUE,DATA_FLAG,UNITS
LIS,E,1,RIV STG,20260114 1800,20260114 1800,13.31, ,FEET
LIS,E,1,RIV STG,20260114 2200,20260114 2200,---, ,FEET
LIS,E,1,RIV STG,20260115 0000,20260115 0000,13.27, ,FEET
"""


class CannedSource(GaugeSource):
    def __init__(self, rows):
        self._rows = rows

    def readings(self, start_utc, end_utc):
        return [r for r in self._rows if start_utc <= r.time_utc <= end_utc]


def test_parse_skips_missing_values():
    rows = parse_cdec_csv(CANNED)
    assert len(rows) == 2  # '---' row dropped, never zeroed
    assert [r.stage_ft for r in rows] == [13.31, 13.27]


def test_parse_converts_pst_to_utc():
    # CDEC is PST year-round: 2026-01-15 00:00 PST == 08:00 UTC.
    last = parse_cdec_csv(CANNED)[-1]
    assert last.time_utc == datetime(2026, 1, 15, 8, 0, tzinfo=timezone.utc)


def test_parse_applies_datum_and_units():
    rows = parse_cdec_csv(CANNED, gauge_zero_navd88_ft=0.16)
    assert abs(rows[-1].wse_navd88_m - (13.27 + 0.16) * M_PER_FT) < 1e-9
    # default zero-offset path: feet -> meters only
    assert abs(parse_cdec_csv(CANNED)[-1].wse_navd88_m - 13.27 * M_PER_FT) < 1e-9


def test_nearest_reading_to_overpass():
    src = CannedSource(parse_cdec_csv(CANNED))
    overpass = datetime(2026, 1, 15, 1, 58, 43, tzinfo=timezone.utc)  # T015843Z
    best = src.wse_navd88_m(overpass)
    assert best.stage_ft == 13.31  # 18:00 PST (02:00 UTC) beats 00:00 PST (08:00 UTC)


def test_gap_tolerance_raises():
    src = CannedSource(parse_cdec_csv(CANNED))
    with pytest.raises(LookupError):
        src.wse_navd88_m(datetime(2026, 2, 1, tzinfo=timezone.utc))


def test_naive_datetime_rejected():
    src = CannedSource(parse_cdec_csv(CANNED))
    with pytest.raises(ValueError):
        src.wse_navd88_m(datetime(2026, 1, 15, 2, 0))  # no tzinfo


def test_cdec_url_shape():
    from datetime import date

    url = CdecStation()._url(date(2026, 1, 14), date(2026, 1, 16))
    assert "Stations=LIS" in url
    assert "SensorNums=1" in url
    assert "dur_code=E" in url
    assert "Start=2026-01-14" in url and "End=2026-01-16" in url
