"""MassDOT IMPACT ArcGIS REST adapter — Phase 4.5.5.

First concrete implementation of :class:`IncidentProvider`
(extension point 3-style, mirrored from imagery's
:class:`ImageryProvider`). Selected by ADR 0007.

MassDOT publishes its crash records through an ArcGIS REST service
partitioned per calendar year:

    https://gis.massdot.state.ma.us/arcgis/rest/services/
        CrashClosedYear/CrashClosedYear<YEAR>/FeatureServer/0/query

Each year's FeatureServer layer carries the same field shape, so the
adapter sweeps the configured year range and merges the results into a
single ``Iterator[IncidentRecord]``. The endpoint accepts WGS84
envelopes via ``inSR=4326`` and returns WGS84 geometry via
``outSR=4326`` — no client-side reprojection needed.

CI never hits the live network: tests live in
:mod:`massdot_impact_test` under ``ingestion/incidents/cassettes/``
with ``vcrpy`` recordings. The recorded cassettes are bbox-scoped to
Cambridge to keep the payload small.

Field map (FeatureServer attribute → :class:`IncidentRecord` field):

  CRASH_NUMB              -> provider_incident_id (stringified)
  CRASH_DATE              -> incident_at (UTC, from epoch ms)
  CRASH_SEVERITY_DESCR    -> severity (mapped via _SEVERITY_MAP)
  geometry.x / geometry.y -> lon / lat
  remaining attributes    -> metadata (jsonb)
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime
from typing import Any, Final

import httpx

from ingestion.incidents.provider import (
    BoundingBox,
    IncidentRecord,
    IncidentSeverity,
)

_FEATURE_SERVER_BASE: Final[str] = (
    "https://gis.massdot.state.ma.us/arcgis/rest/services/"
    "CrashClosedYear/CrashClosedYear{year}/FeatureServer/0/query"
)

# Field set requested from each FeatureServer layer. Kept small so the
# cassette stays compact; expand here if a future scorer needs more
# metadata (e.g., manner-of-collision, contributing factors).
_OUT_FIELDS: Final[tuple[str, ...]] = (
    "CRASH_NUMB",
    "CRASH_DATE",
    "CRASH_SEVERITY_DESCR",
    "MAX_INJR_SVRTY_CL",
    "LAT",
    "LON",
)

# MassDOT severity descriptions -> StreetSense's canonical taxonomy.
# The MassDOT vocabulary collapses KABCO-A/B/C into one "Non-fatal
# injury" bucket; we mirror that mapping into IncidentSeverity.INJURY.
_SEVERITY_MAP: Final[dict[str, IncidentSeverity]] = {
    "Fatal injury": IncidentSeverity.FATAL,
    "Non-fatal injury": IncidentSeverity.INJURY,
    "Non-fatal injury - Suspected serious injury": IncidentSeverity.INJURY,
    "Non-fatal injury - Suspected minor injury": IncidentSeverity.INJURY,
    "Non-fatal injury - Possible injury": IncidentSeverity.INJURY,
    "Property damage only (none injured)": IncidentSeverity.PROPERTY_DAMAGE_ONLY,
    "Not Reported": IncidentSeverity.UNKNOWN,
    "Unknown": IncidentSeverity.UNKNOWN,
    "Reported but invalid": IncidentSeverity.UNKNOWN,
}

# Per-page record cap. MassDOT's default maxRecordCount is 1000; the
# adapter pages through the result set using objectIdFieldName, which
# the FeatureServer exposes by default. 1000 keeps the response under
# ~1 MB on the wire for Cambridge-sized bbox queries.
_PAGE_SIZE: Final[int] = 1000

# Conservative rate limit. MassDOT does not publish a per-key ceiling
# for the public service; 30 req/min is gentle for a backfill that
# only runs weekly.
_DEFAULT_RATE_LIMIT_PER_MINUTE: Final[int] = 30


def _epoch_ms_to_utc(value: int | float) -> datetime:
    """MassDOT CRASH_DATE is integer ms since epoch (UTC)."""
    return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)


def _map_severity(raw: str | None) -> IncidentSeverity:
    if raw is None:
        return IncidentSeverity.UNKNOWN
    return _SEVERITY_MAP.get(raw.strip(), IncidentSeverity.UNKNOWN)


def _bbox_to_envelope(bbox: BoundingBox) -> str:
    """ArcGIS envelope param is ``minLon,minLat,maxLon,maxLat``."""
    return f"{bbox.min_lon:.6f},{bbox.min_lat:.6f},{bbox.max_lon:.6f},{bbox.max_lat:.6f}"


class _TokenBucket:
    """Token-bucket rate limiter, sized in requests per minute.

    Identical shape to ``ingestion.imagery.mapillary._TokenBucket``; the
    duplication is intentional — the two providers have different
    upstream ceilings and would diverge if one tunes its limit. A
    shared abstraction in ``ingestion/_rate.py`` is a follow-up if a
    third provider arrives.
    """

    def __init__(self, capacity_per_minute: int) -> None:
        self._capacity = capacity_per_minute
        self._tokens: float = float(capacity_per_minute)
        self._refill_rate = capacity_per_minute / 60.0
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                float(self._capacity),
                self._tokens + (now - self._last_refill) * self._refill_rate,
            )
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait_s = (1.0 - self._tokens) / self._refill_rate
        time.sleep(wait_s)
        self.acquire()


class MassDOTImpactProvider:
    """MassDOT IMPACT implementation of :class:`IncidentProvider`.

    Constructed with an explicit year range (or a sensible default
    spanning the last 5 years of closed-year data). The adapter sweeps
    each year's FeatureServer layer in turn, yielding one
    :class:`IncidentRecord` per crash whose date falls inside ``within``
    (if supplied). Pages of up to ``_PAGE_SIZE`` records are fetched
    via ``resultOffset`` for layers whose record count exceeds one page.
    """

    name: str = "massdot-impact"

    def __init__(
        self,
        *,
        years: Iterable[int] | None = None,
        client: httpx.Client | None = None,
        rate_limit_per_minute: int = _DEFAULT_RATE_LIMIT_PER_MINUTE,
    ) -> None:
        # CrashClosedYear publishes 2002..2019 at time of writing
        # (MassDOT lags in publishing finalized year cohorts). The
        # default sweeps the most recent five fully-closed years.
        self._years: tuple[int, ...] = (
            tuple(years)
            if years is not None
            else (
                2015,
                2016,
                2017,
                2018,
                2019,
            )
        )
        self._client = client or httpx.Client(http2=True, timeout=30.0)
        self._owns_client = client is None
        self._rate_limiter = _TokenBucket(rate_limit_per_minute)

    def __enter__(self) -> MassDOTImpactProvider:
        return self

    def __exit__(self, *_: object) -> None:
        if self._owns_client:
            self._client.close()

    def fetch_for_bbox(
        self,
        bbox: BoundingBox,
        *,
        within: tuple[date, date] | None = None,
    ) -> Iterator[IncidentRecord]:
        """Stream MassDOT IMPACT crash records inside ``bbox`` / ``within``."""
        envelope = _bbox_to_envelope(bbox)
        for year in self._years:
            if within is not None:
                year_start = date(year, 1, 1)
                year_end = date(year, 12, 31)
                if year_end < within[0] or year_start > within[1]:
                    continue
            yield from self._fetch_year(year, envelope=envelope, within=within)

    def _fetch_year(
        self,
        year: int,
        *,
        envelope: str,
        within: tuple[date, date] | None,
    ) -> Iterator[IncidentRecord]:
        url = _FEATURE_SERVER_BASE.format(year=year)
        offset = 0
        while True:
            self._rate_limiter.acquire()
            response = self._client.get(
                url,
                params={
                    "where": "1=1",
                    "geometry": envelope,
                    "geometryType": "esriGeometryEnvelope",
                    "inSR": "4326",
                    "outFields": ",".join(_OUT_FIELDS),
                    "returnGeometry": "true",
                    "outSR": "4326",
                    "resultRecordCount": str(_PAGE_SIZE),
                    "resultOffset": str(offset),
                    "f": "json",
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            features = payload.get("features", [])
            if not features:
                return
            for raw in features:
                record = self._marshal_feature(raw, within=within)
                if record is not None:
                    yield record
            # ArcGIS signals last page either by ``exceededTransferLimit``
            # = False or by returning fewer than _PAGE_SIZE features.
            if not payload.get("exceededTransferLimit"):
                return
            if len(features) < _PAGE_SIZE:
                return
            offset += len(features)

    def _marshal_feature(
        self,
        raw: dict[str, Any],
        *,
        within: tuple[date, date] | None,
    ) -> IncidentRecord | None:
        attrs = raw.get("attributes", {})
        geom = raw.get("geometry", {})

        crash_number = attrs.get("CRASH_NUMB")
        crash_date_ms = attrs.get("CRASH_DATE")
        if crash_number is None or crash_date_ms is None:
            return None

        incident_at = _epoch_ms_to_utc(crash_date_ms)
        if within is not None:
            d = incident_at.date()
            if d < within[0] or d > within[1]:
                return None

        # Prefer the geocoded point geometry over the LAT/LON attribute
        # columns: the geometry is the canonical authoritative location
        # MassDOT uses for the rendered web map, and a small fraction of
        # records have LAT/LON populated but no geometry (or vice
        # versa). Fall back when one is missing.
        lat = geom.get("y") if geom else None
        lon = geom.get("x") if geom else None
        if lat is None:
            lat = attrs.get("LAT")
        if lon is None:
            lon = attrs.get("LON")
        if lat is None or lon is None:
            return None

        severity = _map_severity(attrs.get("CRASH_SEVERITY_DESCR"))

        metadata = {
            k: attrs[k]
            for k in (
                "CRASH_SEVERITY_DESCR",
                "MAX_INJR_SVRTY_CL",
            )
            if k in attrs and attrs[k] is not None
        }

        return IncidentRecord(
            provider=self.name,
            provider_incident_id=str(crash_number),
            lat=float(lat),
            lon=float(lon),
            incident_at=incident_at,
            severity=severity,
            metadata=metadata,
        )


__all__ = ["MassDOTImpactProvider"]
