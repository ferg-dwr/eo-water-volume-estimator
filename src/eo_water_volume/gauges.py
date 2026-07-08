"""Gauge-based water-surface elevation (WSE): the fix for the planar-WSE error.

A `GaugeSource` answers one question: what was the water-surface elevation, in
NAVD88 meters, at a station near a given UTC time? The volume pipeline swaps
`wse_from_perimeter(...)` for a gauge reading and the dominant (~+/-40%) error
source is replaced by a measured value.

Shaped as a port (like WaterDataSource) so the implementation can later be
promoted to a shared gauge library across ferg-dwr projects without touching
the volume code -- see ROADMAP "Deferred".

Datum facts verified 2026-07-08 (CDEC station page; USGS site service):
  - CDEC LIS  (Yolo Bypass at Lisbon, Toe Drain, mid-bypass):
      gauge zero = 0.0 ft NAVD88 -> stage in feet IS NAVD88 elevation in feet.
      Vertical datum changed 2006-10-01; pre-2006 data is NOT on this datum.
  - USGS 11455140 (Toe Drain at Liberty Island, tidal south end):
      gauge zero = 0.16 ft NAVD88 (+/-0.1 ft) -> WSE = stage + 0.16 ft.
      (USGS implementation lands in a follow-up module, parser verified
      against a live sample first.)

Pure stdlib: no new dependencies. CDEC times are PST year-round (UTC-8, no
DST); readings are converted to aware UTC datetimes at parse time.
"""

from __future__ import annotations

import csv
import io
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

M_PER_FT = 0.3048
PST = timezone(timedelta(hours=-8))  # CDEC reports PST year-round (no DST)


@dataclass(frozen=True)
class GaugeReading:
    """One stage observation, datum-corrected to NAVD88 meters."""

    station: str
    time_utc: datetime  # timezone-aware, UTC
    stage_ft: float  # as reported by the source
    wse_navd88_m: float  # datum-corrected water-surface elevation


class GaugeSource(ABC):
    """The interface the volume pipeline depends on for gauge WSE."""

    @abstractmethod
    def readings(self, start_utc: datetime, end_utc: datetime) -> list[GaugeReading]:
        """All readings in [start_utc, end_utc], sorted by time."""

    def wse_navd88_m(
        self,
        when_utc: datetime,
        max_gap: timedelta = timedelta(hours=3),
    ) -> GaugeReading:
        """The reading nearest `when_utc`; error if none within `max_gap`.

        Returns the full GaugeReading (not a bare float) so callers can stamp
        the gauge time and station into output provenance.
        """
        if when_utc.tzinfo is None:
            raise ValueError("when_utc must be timezone-aware (pass UTC).")
        window = self.readings(when_utc - max_gap, when_utc + max_gap)
        if not window:
            raise LookupError(
                f"No gauge readings within {max_gap} of {when_utc.isoformat()}."
            )
        best = min(window, key=lambda r: abs(r.time_utc - when_utc))
        if abs(best.time_utc - when_utc) > max_gap:
            raise LookupError(
                f"Nearest reading {best.time_utc.isoformat()} is farther than "
                f"{max_gap} from {when_utc.isoformat()}."
            )
        return best


def parse_cdec_csv(
    text: str,
    gauge_zero_navd88_ft: float = 0.0,
) -> list[GaugeReading]:
    """Parse the CDEC CSVDataServlet response into datum-corrected readings.

    Verified against a live response (2026-07-08), header:
      STATION_ID, DURATION, SENSOR_NUMBER,
      SENSOR_TYPE, DATE TIME, OBS DATE,
      VALUE, DATA_FLAG, UNITS
    Times are PST year-round; VALUE is stage in FEET. Non-numeric VALUEs
    (missing data markers like '---') are skipped, not zeroed.
    """
    out: list[GaugeReading] = []
    for row in csv.DictReader(io.StringIO(text)):
        raw = (row.get("VALUE") or "").strip()
        try:
            stage_ft = float(raw)
        except ValueError:
            continue  # missing / flagged observation
        t = datetime.strptime(row["DATE TIME"].strip(), "%Y%m%d %H%M")
        t_utc = t.replace(tzinfo=PST).astimezone(timezone.utc)
        wse_m = (stage_ft + gauge_zero_navd88_ft) * M_PER_FT
        out.append(
            GaugeReading(
                station=row["STATION_ID"].strip(),
                time_utc=t_utc,
                stage_ft=stage_ft,
                wse_navd88_m=wse_m,
            )
        )
    out.sort(key=lambda r: r.time_utc)
    return out


class CdecStation(GaugeSource):
    """A CDEC station via the public CSVDataServlet (no auth).

    Defaults are the verified Lisbon setup: station LIS, sensor 1 (river
    stage), event-frequency data, gauge zero at 0.0 ft NAVD88.
    """

    BASE = "https://cdec.water.ca.gov/dynamicapp/req/CSVDataServlet"

    def __init__(
        self,
        station: str = "LIS",
        sensor: int = 1,
        dur_code: str = "E",
        gauge_zero_navd88_ft: float = 0.0,
    ):
        self.station = station
        self.sensor = sensor
        self.dur_code = dur_code
        self.gauge_zero_navd88_ft = gauge_zero_navd88_ft

    def _url(self, start: date, end: date) -> str:
        q = urllib.parse.urlencode(
            {
                "Stations": self.station,
                "SensorNums": self.sensor,
                "dur_code": self.dur_code,
                "Start": start.isoformat(),
                "End": end.isoformat(),
            }
        )
        return f"{self.BASE}?{q}"

    def readings(self, start_utc: datetime, end_utc: datetime) -> list[GaugeReading]:
        # CDEC's Start/End are PST dates; widen by a day each side so the
        # UTC window is fully covered, then trim precisely.
        start_d = (start_utc.astimezone(PST) - timedelta(days=1)).date()
        end_d = (end_utc.astimezone(PST) + timedelta(days=1)).date()
        with urllib.request.urlopen(self._url(start_d, end_d), timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        rows = parse_cdec_csv(text, self.gauge_zero_navd88_ft)
        return [r for r in rows if start_utc <= r.time_utc <= end_utc]
