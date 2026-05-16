"""Integration tests for ``GET /runs`` (Task 3.3 backend prep).

A small list endpoint the RunPicker frontend component (Task 3.3) needs
to populate its two dropdowns. Returns every ``scoring_runs`` row
ordered newest-first, with the same six-field provenance bundle that
the delta endpoint already ships
(:class:`api.schemas.ScoringRunMetadata`).

Why a new endpoint rather than scraping from the delta path: the delta
endpoint requires the caller to *already know* two run UUIDs. The
picker has no such prior knowledge — it needs to discover them.

Test surface mirrors the Task 2.4 integration tests' shape:

* Happy path — two seeded runs come back in DESC timestamp order with
  full provenance.
* Empty case — zero runs returns ``{"runs": []}``, not 404 (the
  endpoint exists; the list is just empty).
* Ordering is DESC by ``scoring_run_timestamp`` so the picker can show
  the most recent run first without further sorting.

Skipped without ``DATABASE_URL`` per ``tests/api/conftest.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import psycopg
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


_RUN_OLD_TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
_RUN_NEW_TS = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)
_OSM_SNAPSHOT_DATE = date(2026, 5, 1)
_IMAGERY_START = date(2025, 11, 1)
_IMAGERY_END = date(2026, 5, 1)
_PERCEPTION_VERSION = "stand-in-onnx-0.1.0"
_PROPAGATION_VERSION = "pagerank-diffusion-0.1.0"


def _insert_scoring_run(
    cur: psycopg.Cursor[Any], run_timestamp: datetime, *, notes: str
) -> UUID:
    cur.execute(
        """
        INSERT INTO scoring_runs (
            scoring_run_timestamp,
            perception_model_version,
            osm_snapshot_date,
            imagery_capture_window,
            propagation_algorithm_version,
            notes
        )
        VALUES (
            %s, %s, %s, daterange(%s, %s, '[)'),
            %s, %s
        )
        RETURNING id
        """,
        (
            run_timestamp,
            _PERCEPTION_VERSION,
            _OSM_SNAPSHOT_DATE,
            _IMAGERY_START,
            _IMAGERY_END,
            _PROPAGATION_VERSION,
            notes,
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


@pytest.fixture
def seed_two_runs_for_listing(
    owner_conn: psycopg.Connection[Any],
) -> tuple[UUID, UUID]:
    """Insert two scoring runs at distinct timestamps. Returns ``(old_id, new_id)``."""
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs CASCADE")
        # Insert OLD first so DB physical-order doesn't trivially match expected order.
        run_old = _insert_scoring_run(cur, _RUN_OLD_TS, notes="task 3.3 list — old")
        run_new = _insert_scoring_run(cur, _RUN_NEW_TS, notes="task 3.3 list — new")
    owner_conn.commit()
    return run_old, run_new


@pytest.fixture
def clear_runs(owner_conn: psycopg.Connection[Any]) -> None:
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs CASCADE")
    owner_conn.commit()


@pytest.mark.asyncio
async def test_list_runs_happy_path_returns_200_with_typed_body(
    seed_two_runs_for_listing: tuple[UUID, UUID],
    api_client: AsyncClient,
) -> None:
    _old, _new = seed_two_runs_for_listing
    response = await api_client.get("/runs")
    assert response.status_code == 200
    body = response.json()
    assert "runs" in body
    assert isinstance(body["runs"], list)
    assert len(body["runs"]) == 2
    first = body["runs"][0]
    # The six provenance fields are all present.
    expected_keys = {
        "scoring_run_id",
        "scoring_run_timestamp",
        "perception_model_version",
        "osm_snapshot_date",
        "imagery_capture_window_start",
        "imagery_capture_window_end",
        "propagation_algorithm_version",
    }
    assert expected_keys.issubset(set(first.keys()))


@pytest.mark.asyncio
async def test_list_runs_orders_newest_first(
    seed_two_runs_for_listing: tuple[UUID, UUID],
    api_client: AsyncClient,
) -> None:
    old_id, new_id = seed_two_runs_for_listing
    response = await api_client.get("/runs")
    assert response.status_code == 200
    runs = response.json()["runs"]
    assert UUID(runs[0]["scoring_run_id"]) == new_id
    assert UUID(runs[1]["scoring_run_id"]) == old_id


@pytest.mark.asyncio
async def test_list_runs_empty_returns_200_with_empty_list(
    clear_runs: None,
    api_client: AsyncClient,
) -> None:
    del clear_runs
    response = await api_client.get("/runs")
    assert response.status_code == 200
    body = response.json()
    assert body == {"runs": []}


@pytest.mark.asyncio
async def test_list_runs_carries_full_provenance(
    seed_two_runs_for_listing: tuple[UUID, UUID],
    api_client: AsyncClient,
) -> None:
    del seed_two_runs_for_listing
    response = await api_client.get("/runs")
    runs = response.json()["runs"]
    for r in runs:
        assert r["perception_model_version"] == _PERCEPTION_VERSION
        assert r["propagation_algorithm_version"] == _PROPAGATION_VERSION
        assert r["osm_snapshot_date"] == _OSM_SNAPSHOT_DATE.isoformat()
        assert r["imagery_capture_window_start"] == _IMAGERY_START.isoformat()
        assert r["imagery_capture_window_end"] == _IMAGERY_END.isoformat()
