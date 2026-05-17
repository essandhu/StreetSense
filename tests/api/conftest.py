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
def seed_segment(owner_conn: psycopg.Connection[Any], cambridge_city_id: Any) -> UUID:
    """Insert one road_segments row and return its UUID.

    Cleans up first so the fixture can be requested by every test in the
    module without unique-constraint collisions.
    """
    geom = LineString([(-71.10, 42.36), (-71.09, 42.37), (-71.08, 42.38)])
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        cur.execute(
            """
            INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
            VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb, %s)
            RETURNING id
            """,
            (
                999_001,
                wkb.dumps(geom),
                '{"highway": "primary", "name": "Fixture A"}',
                cambridge_city_id,
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

    Phase 1: `osm`.
    Phase 2: `solar_position` (compute source — migration 0005).
    Phase 3: `imagery` (migration 0009) with real metadata; and
    `perception_model` once `make seed-model` has run.
    Phase 4: `incidents` (migration 0014) populated by
    `make ingest-incidents`; `propagation_algorithm` (migration 0014).

    The Phase 4 freshness endpoint surfaces all six.
    """
    with owner_conn.cursor() as cur:
        cur.execute("DELETE FROM data_sources")
        cur.execute(
            """
            INSERT INTO data_sources (name, last_ingested_at, metadata)
            VALUES
                ('osm',              now() - interval '1 hour',
                 '{"source_url": "file://osm"}'::jsonb),
                ('imagery',          now() - interval '5 minutes',
                 '{"kind": "fetch", "provider": "mapillary", "adr": "0005-imagery-provider", "license": "CC-BY-SA"}'::jsonb),
                ('solar_position',   now(),
                 '{"kind": "compute", "library": "pvlib", "model": "nrel-spa"}'::jsonb),
                ('perception_model', now() - interval '10 minutes',
                 '{"kind": "model", "perception_model_version": "lane-marking-standin-deadbeef", "object_key": "lane-marking-standin-deadbeef/lane-marking-standin.onnx", "bucket": "streetsense-models"}'::jsonb),
                ('incidents',        now() - interval '2 minutes',
                 '{"kind": "fetch", "provider": "massdot-impact", "adr": "0007-incident-dataset", "license": "public-records"}'::jsonb),
                ('propagation_algorithm', now() - interval '30 seconds',
                 '{"kind": "compute", "library": "streetsense_propagator", "adr": "0006-propagation-algorithm", "algorithm": "pagerank-diffusion"}'::jsonb)
            """
        )
    owner_conn.commit()
