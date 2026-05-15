"""Integration tests for `ingestion.imagery.job` — Task 3.2.8 (test first).

Strategy
--------
Phase 3.2's vcrpy cassettes already cover the Mapillary HTTP contract
(see ``ingestion/imagery/mapillary_test.py``). For the *persistence*
tests we don't re-exercise the HTTP layer; we materialize the cassette
responses into memory once via ``MapillaryProvider``, then pass a
``_PreloadedProvider`` adapter into ``ingest_imagery``. That isolates
the persistence + MinIO upload behavior from VCR's connection-level
monkeypatching — VCR 8.x's ``ignore_hosts`` filter runs after the
urllib3 stub has already intercepted, which breaks the MinIO SDK's
direct urllib3 use.

Requires Postgres + MinIO. The ``migrated_db`` fixture handles schema.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
import vcr
from minio import Minio
from shapely import wkb
from shapely.geometry import LineString

from ingestion.imagery.job import (
    ImageryIngestConfig,
    _MinIOClient,
    ingest_imagery,
)
from ingestion.imagery.mapillary import MapillaryProvider
from ingestion.imagery.provider import ImageryReference, Waypoint

pytestmark = pytest.mark.integration


# Match the bbox the cassettes were recorded against (a single
# waypoint at (42.372, -71.105)).
_FIXTURE_LAT = 42.372
_FIXTURE_LON = -71.105

_CASSETTE_DIR = Path(__file__).resolve().parents[2] / "ingestion" / "imagery" / "cassettes"
_SCRUBBED_TOKEN_PLACEHOLDER = "MLY|TEST|TEST"


@pytest.fixture(autouse=True)
def _clean_imagery_tables(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_imagery")
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        cur.execute("UPDATE data_sources SET last_ingested_at = NULL WHERE name = 'imagery'")
    owner_conn.commit()


@pytest.fixture
def seeded_segment(owner_conn: psycopg.Connection[Any]) -> UUID:
    """Insert one segment whose midpoint matches the cassette waypoint.

    The geometry length (~11 m) keeps ``_segment_waypoints`` to a single
    midpoint sample at (_FIXTURE_LAT, _FIXTURE_LON) so the cassette's
    bbox response is the one we hit.
    """
    geom = LineString(
        [
            (_FIXTURE_LON - 0.00005, _FIXTURE_LAT),
            (_FIXTURE_LON, _FIXTURE_LAT),
            (_FIXTURE_LON + 0.00005, _FIXTURE_LAT),
        ]
    )
    with owner_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO road_segments (osm_way_id, geometry, attrs)
            VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), '{}'::jsonb)
            RETURNING id
            """,
            (777_001, wkb.dumps(geom)),
        )
        row = cur.fetchone()
        assert row is not None
    owner_conn.commit()
    return row[0]


def _minio_client_from_env() -> Minio:
    return Minio(
        os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.environ.get("MINIO_ROOT_USER", "streetsense"),
        secret_key=os.environ.get("MINIO_ROOT_PASSWORD", "streetsense"),
        secure=False,
    )


@pytest.fixture
def fresh_bucket() -> str:
    """Per-test bucket so previous-run objects don't bleed in."""
    bucket = "streetsense-imagery-test"
    client = _minio_client_from_env()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    for obj in client.list_objects(bucket, recursive=True):
        client.remove_object(bucket, obj.object_name)
    return bucket


# ---------------------------------------------------------------------------
# Cassette materialization: run the live cassette-controlled provider ONCE,
# capture the references + bytes, then hand them to the persistence layer
# via a preloaded adapter.
# ---------------------------------------------------------------------------
class _PreloadedProvider:
    """Provider adapter that returns pre-materialized references / bytes.

    Used by the persistence tests so MinIO + Postgres calls don't run
    inside a vcrpy cassette context.
    """

    name = "mapillary"

    def __init__(
        self,
        references: list[ImageryReference],
        bytes_by_id: dict[str, bytes],
    ) -> None:
        self._references = references
        self._bytes_by_id = bytes_by_id

    def fetch_for_waypoints(
        self,
        waypoints: list[Waypoint],
        *,
        within: tuple[date, date] | None = None,
    ) -> Iterator[ImageryReference]:
        del waypoints, within
        yield from self._references

    def download_bytes(self, reference: ImageryReference) -> bytes:
        return self._bytes_by_id[reference.provider_image_id]


@pytest.fixture
def preloaded_provider(seeded_segment: UUID) -> _PreloadedProvider:
    """Load the cassette once; expose a network-free provider."""
    vcr_cfg = vcr.VCR(
        cassette_library_dir=str(_CASSETTE_DIR),
        record_mode="once",
        match_on=["method", "scheme", "host", "path", "query"],
        filter_query_parameters=[("access_token", _SCRUBBED_TOKEN_PLACEHOLDER)],
        filter_headers=[("authorization", _SCRUBBED_TOKEN_PLACEHOLDER)],
        decode_compressed_response=True,
    )
    waypoint = Waypoint(
        lat=_FIXTURE_LAT,
        lon=_FIXTURE_LON,
        segment_id=seeded_segment,
        sample_index=0,
    )
    with (
        vcr_cfg.use_cassette("download_bytes_returns_image_bytes.yaml"),
        MapillaryProvider(access_token=_SCRUBBED_TOKEN_PLACEHOLDER) as provider,
    ):
        references = list(provider.fetch_for_waypoints([waypoint]))
        assert references, "Cassette returned no references"
        # The cassette has image bytes for the FIRST reference only.
        # The persistence test only needs one round trip per segment to
        # exercise the row write + MinIO upload path; slicing here keeps
        # the test honest about what the cassette covers.
        head = references[:1]
        bytes_by_id = {ref.provider_image_id: provider.download_bytes(ref) for ref in head}
    return _PreloadedProvider(head, bytes_by_id)


def _run_ingest(database_url: str, bucket: str, provider: _PreloadedProvider) -> int:
    summary = ingest_imagery(
        database_url=database_url,
        provider=provider,
        object_store=_MinIOClient(),
        config=ImageryIngestConfig(bucket=bucket),
    )
    return summary.rows_inserted


def test_ingest_writes_rows_and_uploads_objects(
    owner_conn: psycopg.Connection[Any],
    database_url: str,
    seeded_segment: UUID,
    preloaded_provider: _PreloadedProvider,
    fresh_bucket: str,
) -> None:
    inserted = _run_ingest(database_url, fresh_bucket, preloaded_provider)
    assert inserted >= 1, f"Expected at least one row inserted, got {inserted}"

    with owner_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM segment_imagery WHERE segment_id = %s",
            (seeded_segment,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == inserted

    client = _minio_client_from_env()
    with owner_conn.cursor() as cur:
        cur.execute(
            "SELECT object_key FROM segment_imagery WHERE segment_id = %s",
            (seeded_segment,),
        )
        for (object_key,) in cur.fetchall():
            stat = client.stat_object(fresh_bucket, object_key)
            assert stat.size > 0


def test_ingest_is_idempotent(
    owner_conn: psycopg.Connection[Any],
    database_url: str,
    seeded_segment: UUID,
    preloaded_provider: _PreloadedProvider,
    fresh_bucket: str,
) -> None:
    first = _run_ingest(database_url, fresh_bucket, preloaded_provider)
    assert first >= 1
    second = _run_ingest(database_url, fresh_bucket, preloaded_provider)
    assert second == 0, f"Re-running ingest_imagery must be a no-op; inserted {second} new rows"

    with owner_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM segment_imagery WHERE segment_id = %s",
            (seeded_segment,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == first


def test_data_sources_freshness_bumped(
    owner_conn: psycopg.Connection[Any],
    database_url: str,
    seeded_segment: UUID,
    preloaded_provider: _PreloadedProvider,
    fresh_bucket: str,
) -> None:
    del seeded_segment
    _run_ingest(database_url, fresh_bucket, preloaded_provider)

    with owner_conn.cursor() as cur:
        cur.execute("SELECT last_ingested_at FROM data_sources WHERE name = 'imagery'")
        row = cur.fetchone()
    assert row is not None
    assert row[0] is not None, "imagery.last_ingested_at must be bumped by the job"


# --- Regression: per-batch commit -----------------------------------------
# A long-running ingest must persist flushed batches even if a later
# batch raises. The single end-of-job commit pattern lost ~5k uploads
# across two failed Cambridge attempts before this was fixed; see the
# `_flush()` comment in job.py for the failure mode.
class _FailAfterNRefsProvider:
    """Yield the first N references, then raise on the (N+1)th step.

    Used to simulate a transient provider failure mid-stream so the
    test can assert that the already-flushed batches survived.
    """

    name = "mapillary"

    def __init__(
        self,
        references: list[ImageryReference],
        bytes_by_id: dict[str, bytes],
        fail_after_n: int,
    ) -> None:
        self._references = references
        self._bytes_by_id = bytes_by_id
        self._fail_after_n = fail_after_n

    def fetch_for_waypoints(
        self,
        waypoints: list[Waypoint],
        *,
        within: tuple[date, date] | None = None,
    ) -> Iterator[ImageryReference]:
        del waypoints, within
        for idx, ref in enumerate(self._references):
            if idx >= self._fail_after_n:
                raise RuntimeError("simulated mid-stream provider failure")
            yield ref

    def download_bytes(self, reference: ImageryReference) -> bytes:
        return self._bytes_by_id[reference.provider_image_id]


def _synthetic_refs(segment_id: UUID, n: int) -> tuple[list[ImageryReference], dict[str, bytes]]:
    refs = [
        ImageryReference(
            provider="mapillary",
            provider_image_id=f"synthetic-{i}",
            segment_id=segment_id,
            sample_index=i,
            capture_date=date(2025, 1, 1),
            heading_deg=float(i * 30 % 360),
            camera_params={},
        )
        for i in range(n)
    ]
    bytes_by_id = {
        ref.provider_image_id: f"fake-image-bytes-{i}".encode() for i, ref in enumerate(refs)
    }
    return refs, bytes_by_id


def test_flushed_batches_persist_when_run_fails_midstream(
    owner_conn: psycopg.Connection[Any],
    database_url: str,
    seeded_segment: UUID,
    fresh_bucket: str,
) -> None:
    """A failure after the first batch flushes must leave the first
    batch's rows durable in segment_imagery — proving per-batch commit."""
    refs, bytes_by_id = _synthetic_refs(seeded_segment, n=3)
    provider = _FailAfterNRefsProvider(refs, bytes_by_id, fail_after_n=2)

    with pytest.raises(RuntimeError, match="simulated mid-stream provider failure"):
        ingest_imagery(
            database_url=database_url,
            provider=provider,
            object_store=_MinIOClient(),
            config=ImageryIngestConfig(bucket=fresh_bucket, insert_batch_size=2),
        )

    # The first batch (2 refs) flushed and must be visible from a
    # separate connection. The 3rd ref triggered the failure before
    # the batch filled, so only 2 rows persist.
    with owner_conn.cursor() as cur:
        cur.execute(
            "SELECT provider_image_id FROM segment_imagery WHERE segment_id = %s ORDER BY sample_index",
            (seeded_segment,),
        )
        rows = [r[0] for r in cur.fetchall()]
    assert rows == ["synthetic-0", "synthetic-1"], (
        f"Expected the first batch to survive the mid-stream failure; got {rows}"
    )

    # data_sources.imagery.last_ingested_at must NOT be bumped — the
    # whole run failed, even though some batches were durable.
    with owner_conn.cursor() as cur:
        cur.execute("SELECT last_ingested_at FROM data_sources WHERE name = 'imagery'")
        row = cur.fetchone()
    assert row is not None
    assert row[0] is None, (
        "imagery freshness must reflect a full successful run, not a partial commit"
    )
