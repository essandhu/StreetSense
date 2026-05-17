"""``GET /admin/freshness`` — global + per-city ingestion freshness.

Phase 1-4: a flat ``sources`` list reporting global ``data_sources``
rows. Phase 4b Task 3.5 extends the response with a per-city block:
the spatial tables (road_segments, segment_imagery, incidents,
scoring_runs) all carry ``city_id`` after migration 0017, so a
``max(timestamp)`` aggregate per city yields per-source freshness
without doubling state in ``data_sources``.

The list-shaped envelope from Phase 1 stays for backwards
compatibility — the kind=compute / kind=model sources in
``data_sources`` (solar_position, perception_model,
propagation_algorithm) are genuinely global and live in ``sources``.
The new per-city block adds the kind=fetch sources (osm, imagery,
incidents) plus a synthetic ``scoring_run`` entry derived from
``scoring_runs.scoring_run_timestamp``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from api.db import conn
from api.schemas import FreshnessEntry, FreshnessReport

router = APIRouter(prefix="/admin", tags=["admin"])


# One query per city x source would be N x 4 round-trips. The
# per-city aggregates are small enough that a single query with
# correlated sub-selects (or LATERAL joins) hits PG once. Each
# sub-select uses a composite (city_id, ...) index added by
# migration 0017, so they're each one bounded index scan.
_PER_CITY_FRESHNESS_SQL = """
SELECT
    c.slug AS slug,
    (SELECT max(rs.created_at)
       FROM road_segments rs
       WHERE rs.city_id = c.id)                AS osm,
    (SELECT max(si.ingested_at)
       FROM segment_imagery si
       WHERE si.city_id = c.id)                AS imagery,
    (SELECT max(i.ingested_at)
       FROM incidents i
       WHERE i.city_id = c.id)                 AS incidents,
    (SELECT max(sr.scoring_run_timestamp)
       FROM scoring_runs sr
       WHERE sr.city_id = c.id)                AS scoring_run
FROM cities c
ORDER BY c.slug
"""


@router.get("/freshness", response_model=FreshnessReport)
async def freshness() -> FreshnessReport:
    async with conn() as c, c.cursor() as cur:
        # Global sources (Phase 1-4 shape). One row per registered source.
        await cur.execute("SELECT name, last_ingested_at, metadata FROM data_sources ORDER BY name")
        source_rows = await cur.fetchall()

        # Per-city freshness — one row per city, columns are the
        # per-city sources.
        await cur.execute(_PER_CITY_FRESHNESS_SQL)
        city_rows = await cur.fetchall()

    sources = [
        FreshnessEntry(name=name, last_ingested_at=last, metadata=metadata or {})
        for name, last, metadata in source_rows
    ]
    cities: dict[str, dict[str, datetime | None]] = {
        row[0]: {
            "osm": row[1],
            "imagery": row[2],
            "incidents": row[3],
            "scoring_run": row[4],
        }
        for row in city_rows
    }
    return FreshnessReport(
        sources=sources,
        cities=cities,
        server_time=datetime.now(UTC),
    )
