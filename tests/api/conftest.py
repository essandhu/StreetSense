"""API test fixtures: an httpx AsyncClient against the FastAPI app."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import psycopg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shapely import wkb
from shapely.geometry import LineString

from api.db import close_pool
from api.main import create_app


@pytest_asyncio.fixture
async def api_client(database_url: str) -> AsyncIterator[AsyncClient]:
    del database_url  # ensures the integration-mode skip fires before app boot
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    await close_pool()


@pytest.fixture
def seed_segment(owner_conn: psycopg.Connection[Any]) -> UUID:
    """Insert one road_segments row and return its UUID.

    Cleans up first so the fixture can be requested by every test in the
    module without unique-constraint collisions.
    """
    geom = LineString([(-71.10, 42.36), (-71.09, 42.37), (-71.08, 42.38)])
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        cur.execute(
            """
            INSERT INTO road_segments (osm_way_id, geometry, attrs)
            VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb)
            RETURNING id
            """,
            (
                999_001,
                wkb.dumps(geom),
                '{"highway": "primary", "name": "Fixture A"}',
            ),
        )
        row = cur.fetchone()
        assert row is not None
        segment_id: UUID = row[0]
    owner_conn.commit()
    return segment_id


@pytest.fixture
def seed_data_sources(owner_conn: psycopg.Connection[Any]) -> None:
    """Insert data_sources rows for the freshness endpoint.

    Phase 2 adds `solar_position` to the registry (migration 0005); the
    `/admin/freshness` endpoint surfaces it alongside `osm` and the
    Phase-3 placeholder `imagery`.
    """
    with owner_conn.cursor() as cur:
        cur.execute("DELETE FROM data_sources")
        cur.execute(
            """
            INSERT INTO data_sources (name, last_ingested_at, metadata)
            VALUES
                ('osm',            now() - interval '1 hour', '{"source_url": "file://osm"}'::jsonb),
                ('imagery',        NULL,                       '{}'::jsonb),
                ('solar_position', now(),                      '{"kind": "compute", "library": "pvlib", "model": "nrel-spa"}'::jsonb)
            """
        )
    owner_conn.commit()
