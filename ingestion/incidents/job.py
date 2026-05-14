"""Historical-incident ingestion job — Phase 4.5.6.

Provider-agnostic ingestion pipeline that:

1. Streams ``IncidentRecord`` objects from a chosen ``IncidentProvider``
   over the configured city's bounding box (and optional date window).
2. Upserts each record into the ``incidents`` PostGIS table using the
   ``(provider, provider_incident_id)`` natural key, so re-runs are
   idempotent at row granularity.
3. Bumps ``data_sources.last_ingested_at`` for the ``incidents`` row
   so ``/admin/freshness`` reflects the run.

Per CLAUDE.md, no ``print`` in shipped code — ``structlog`` events only.
Per ADR 0007, no caller other than this job knows which concrete
provider is being used; swapping the provider is a single import / DI
change at the CLI level.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import structlog
from psycopg.types.json import Jsonb

from ingestion.incidents.provider import BoundingBox, IncidentProvider, IncidentSeverity

log = structlog.get_logger(__name__)


_INSERT_INCIDENT_SQL = """
INSERT INTO incidents (
    provider,
    provider_incident_id,
    geom,
    incident_at,
    severity,
    metadata
)
VALUES (
    %(provider)s,
    %(provider_incident_id)s,
    ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326),
    %(incident_at)s,
    %(severity)s,
    %(metadata)s
)
ON CONFLICT (provider, provider_incident_id) DO NOTHING
"""

_BUMP_DATA_SOURCE_SQL = """
UPDATE data_sources
SET last_ingested_at = COALESCE(
    (SELECT max(incident_at) FROM incidents WHERE provider = %(provider)s),
    last_ingested_at
)
WHERE name = 'incidents'
"""


@dataclass(frozen=True, slots=True)
class IncidentIngestConfig:
    """Tunables for the ingestion run.

    ``within`` filters the provider's output to a date window. ``None``
    means "consider every record the provider yields".

    ``insert_batch_size`` bounds the transaction size; 500 keeps the
    psycopg ``executemany`` batch under the libpq default 1 MB write
    buffer for a typical metadata payload.
    """

    within: tuple[date, date] | None = None
    insert_batch_size: int = 500


@dataclass(frozen=True, slots=True)
class IncidentIngestSummary:
    """Outcome of an ingestion run, suitable for printing or logging."""

    rows_inserted: int
    rows_skipped: int  # already-present rows hit by ON CONFLICT DO NOTHING
    rows_seen: int  # total records yielded by the provider
    elapsed_seconds: float
    earliest_incident_at: datetime | None = None
    latest_incident_at: datetime | None = None
    severity_counts: dict[str, int] = field(default_factory=dict)


def _psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def ingest_incidents(
    *,
    database_url: str,
    provider: IncidentProvider,
    bbox: BoundingBox,
    config: IncidentIngestConfig | None = None,
) -> IncidentIngestSummary:
    """Run the incident ingestion job end-to-end. Returns a summary."""
    cfg = config or IncidentIngestConfig()
    dsn = _psycopg_dsn(database_url)
    start = time.perf_counter()

    rows_inserted = 0
    rows_skipped = 0
    rows_seen = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    severity_counts: dict[str, int] = {s.value: 0 for s in IncidentSeverity}

    log.info(
        "incident_ingest.start",
        provider=provider.name,
        bbox=[bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat],
        within=[cfg.within[0].isoformat(), cfg.within[1].isoformat()] if cfg.within else None,
    )

    with psycopg.connect(dsn) as conn:
        # Snapshot the existing (provider, provider_incident_id) set
        # before insertion so we can count skips accurately. The unique
        # constraint guards correctness; this is only for reporting.
        with conn.cursor() as cur:
            cur.execute(
                "SELECT provider_incident_id FROM incidents WHERE provider = %s",
                (provider.name,),
            )
            existing: set[str] = {row[0] for row in cur.fetchall()}

        batch: list[dict[str, Any]] = []

        def _flush() -> int:
            nonlocal batch
            if not batch:
                return 0
            with conn.cursor() as inner:
                inner.executemany(_INSERT_INCIDENT_SQL, batch)
            count = len(batch)
            batch = []
            conn.commit()
            return count

        for record in provider.fetch_for_bbox(bbox, within=cfg.within):
            rows_seen += 1
            severity_counts[record.severity.value] = (
                severity_counts.get(record.severity.value, 0) + 1
            )
            if record.provider_incident_id in existing:
                rows_skipped += 1
                continue
            existing.add(record.provider_incident_id)  # de-dup within the run too

            incident_at = record.incident_at.astimezone(UTC)
            if earliest is None or incident_at < earliest:
                earliest = incident_at
            if latest is None or incident_at > latest:
                latest = incident_at

            batch.append(
                {
                    "provider": record.provider,
                    "provider_incident_id": record.provider_incident_id,
                    "lat": record.lat,
                    "lon": record.lon,
                    "incident_at": incident_at,
                    "severity": record.severity.value,
                    "metadata": Jsonb(record.metadata),
                }
            )
            if len(batch) >= cfg.insert_batch_size:
                rows_inserted += _flush()

        rows_inserted += _flush()

        with conn.cursor() as cur:
            cur.execute(_BUMP_DATA_SOURCE_SQL, {"provider": provider.name})
        conn.commit()

    elapsed = time.perf_counter() - start

    summary = IncidentIngestSummary(
        rows_inserted=rows_inserted,
        rows_skipped=rows_skipped,
        rows_seen=rows_seen,
        elapsed_seconds=elapsed,
        earliest_incident_at=earliest,
        latest_incident_at=latest,
        severity_counts=severity_counts,
    )
    log.info(
        "incident_ingest.done",
        provider=provider.name,
        rows_inserted=rows_inserted,
        rows_skipped=rows_skipped,
        rows_seen=rows_seen,
        seconds=round(elapsed, 3),
        earliest_incident_at=earliest.isoformat() if earliest else None,
        latest_incident_at=latest.isoformat() if latest else None,
        severity_counts=severity_counts,
    )
    return summary


__all__ = [
    "IncidentIngestConfig",
    "IncidentIngestSummary",
    "ingest_incidents",
]
