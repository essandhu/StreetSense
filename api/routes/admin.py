"""GET /admin/freshness — latest ingestion timestamp per registered source.

The response is a list-shaped envelope so Phase 3 can register additional
sources without a breaking API change.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from api.db import conn
from api.schemas import FreshnessEntry, FreshnessReport

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/freshness", response_model=FreshnessReport)
async def freshness() -> FreshnessReport:
    async with conn() as c, c.cursor() as cur:
        await cur.execute("SELECT name, last_ingested_at, metadata FROM data_sources ORDER BY name")
        rows = await cur.fetchall()

    sources = [
        FreshnessEntry(name=name, last_ingested_at=last, metadata=metadata or {})
        for name, last, metadata in rows
    ]
    return FreshnessReport(sources=sources, server_time=datetime.now(UTC))
