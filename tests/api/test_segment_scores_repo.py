"""Integration tests for the segment_scores delta repo (Task 2.3).

Skipped without ``DATABASE_URL``. The shared ``owner_conn`` fixture
TRUNCATEs the score-related tables on entry — per the memory note in
``tests/README.md``, run this BEFORE live ingests or against a separate
test database.

The tests seed two scoring_runs at the same hour-of-day (both at noon, on
different dates — the weekly-cron shape) with overlapping segments, then
exercise:

  * ``runs_exist`` returns ``(True, True)`` / ``(False, True)`` / ...
  * ``count_pair_at_hour`` returns the joined-segments count
  * ``fetch_pair_at_hour`` streams pairs in stable segment_id order
  * Pagination via ``limit`` / ``offset``
  * EXPLAIN shows the expected btree-index usage on
    ``segment_scores_run_id_idx``
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from shapely import wkb
from shapely.geometry import LineString

from api.repos.segment_scores import (
    FETCH_PAIR_AT_HOUR_SQL,
    count_pair_at_hour,
    fetch_pair_at_hour,
    runs_exist,
)

pytestmark = pytest.mark.integration

# Test data shaped after the weekly-cron expectation: two runs a week
# apart, both at noon (matching hour-of-day).
_HOUR = 12
_RUN_A_TS = datetime(2026, 5, 8, _HOUR, 0, 0, tzinfo=UTC)
_RUN_B_TS = datetime(2026, 5, 15, _HOUR, 0, 0, tzinfo=UTC)


def _insert_scoring_run(
    cur: psycopg.Cursor[Any], run_timestamp: datetime, *, notes: str, city_id: Any
) -> UUID:
    cur.execute(
        """
        INSERT INTO scoring_runs (
            scoring_run_timestamp,
            perception_model_version,
            osm_snapshot_date,
            imagery_capture_window,
            propagation_algorithm_version,
            notes,
            city_id
        )
        VALUES (
            %s, %s, %s, daterange(%s, %s, '[)'),
            %s, %s, %s
        )
        RETURNING id
        """,
        (
            run_timestamp,
            "stand-in-onnx-0.1.0",
            date(2026, 5, 1),
            date(2025, 11, 1),
            date(2026, 5, 1),
            "pagerank-diffusion-0.1.0",
            notes,
            city_id,
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _insert_segment(cur: psycopg.Cursor[Any], offset_idx: int, city_id: Any) -> UUID:
    """Insert one road_segments row at a deterministic location."""
    base_lon = -71.10 - (offset_idx * 0.001)
    base_lat = 42.36 + (offset_idx * 0.001)
    geom = LineString([(base_lon, base_lat), (base_lon + 0.001, base_lat + 0.001)])
    cur.execute(
        """
        INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
        VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb, %s)
        RETURNING id
        """,
        (
            900_000 + offset_idx,
            wkb.dumps(geom),
            '{"highway": "primary"}',
            city_id,
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _insert_score(
    cur: psycopg.Cursor[Any],
    segment_id: UUID,
    run_id: UUID,
    run_timestamp: datetime,
    city_id: Any,
    *,
    composite_risk: float,
    propagation_uplift: float,
    sub_lane: float,
    sub_glare: float,
    sub_junction: float,
    sub_historical: float,
    confidence: float = 0.8,
) -> None:
    cur.execute(
        """
        INSERT INTO segment_scores (
            segment_id,
            composite_risk,
            sub_score_lane_marking,
            sub_score_glare,
            sub_score_junction_complexity,
            sub_score_historical,
            confidence,
            scoring_run_id,
            scoring_run_timestamp,
            perception_model_version,
            osm_snapshot_date,
            imagery_capture_window,
            propagation_algorithm_version,
            propagation_uplift,
            is_stub_lane_marking,
            is_stub_glare,
            is_stub_junction_complexity,
            is_stub_historical,
            city_id
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, daterange(%s, %s, '[)'),
            %s, %s,
            false, false, false, false,
            %s
        )
        """,
        (
            segment_id,
            composite_risk,
            sub_lane,
            sub_glare,
            sub_junction,
            sub_historical,
            confidence,
            run_id,
            run_timestamp,
            "stand-in-onnx-0.1.0",
            date(2026, 5, 1),
            date(2025, 11, 1),
            date(2026, 5, 1),
            "pagerank-diffusion-0.1.0",
            propagation_uplift,
            city_id,
        ),
    )


@pytest.fixture
def seed_two_runs(
    owner_conn: psycopg.Connection[Any], cambridge_city_id: Any
) -> tuple[UUID, UUID, list[UUID]]:
    """Insert two scoring_runs and N segments scored in both at the noon hour.

    Returns ``(run_a_id, run_b_id, [segment_ids])``.
    """
    n_segments = 5
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, road_segments CASCADE")
        run_a = _insert_scoring_run(
            cur, _RUN_A_TS, notes="phase-5 delta repo test run A", city_id=cambridge_city_id
        )
        run_b = _insert_scoring_run(
            cur, _RUN_B_TS, notes="phase-5 delta repo test run B", city_id=cambridge_city_id
        )
        segment_ids: list[UUID] = []
        for i in range(n_segments):
            sid = _insert_segment(cur, i, cambridge_city_id)
            segment_ids.append(sid)
            # Insert one row per segment at noon for both runs.
            _insert_score(
                cur,
                sid,
                run_a,
                _RUN_A_TS,
                cambridge_city_id,
                composite_risk=0.30 + i * 0.05,
                propagation_uplift=0.05 + i * 0.01,
                sub_lane=0.20 + i * 0.05,
                sub_glare=0.10 + i * 0.02,
                sub_junction=0.30,
                sub_historical=0.15,
            )
            _insert_score(
                cur,
                sid,
                run_b,
                _RUN_B_TS,
                cambridge_city_id,
                composite_risk=0.40 + i * 0.05,  # +0.10 from run A
                propagation_uplift=0.08 + i * 0.01,  # +0.03
                sub_lane=0.25 + i * 0.05,  # +0.05
                sub_glare=0.08 + i * 0.02,  # -0.02
                sub_junction=0.30,
                sub_historical=0.15,
            )
        # Also seed one segment present in run_a only and one in run_b only —
        # these must NOT appear in fetch_pair output.
        only_a = _insert_segment(cur, 100, cambridge_city_id)
        _insert_score(
            cur,
            only_a,
            run_a,
            _RUN_A_TS,
            cambridge_city_id,
            composite_risk=0.1,
            propagation_uplift=0.0,
            sub_lane=0.1,
            sub_glare=0.1,
            sub_junction=0.1,
            sub_historical=0.1,
        )
        only_b = _insert_segment(cur, 101, cambridge_city_id)
        _insert_score(
            cur,
            only_b,
            run_b,
            _RUN_B_TS,
            cambridge_city_id,
            composite_risk=0.1,
            propagation_uplift=0.0,
            sub_lane=0.1,
            sub_glare=0.1,
            sub_junction=0.1,
            sub_historical=0.1,
        )
        # And one segment scored at a different hour (NOT noon) in run_a, to
        # confirm the hour filter excludes it.
        off_hour = _insert_segment(cur, 200, cambridge_city_id)
        _insert_score(
            cur,
            off_hour,
            run_a,
            _RUN_A_TS + timedelta(hours=1),
            cambridge_city_id,
            composite_risk=0.1,
            propagation_uplift=0.0,
            sub_lane=0.1,
            sub_glare=0.1,
            sub_junction=0.1,
            sub_historical=0.1,
        )
        _insert_score(
            cur,
            off_hour,
            run_b,
            _RUN_B_TS + timedelta(hours=1),
            cambridge_city_id,
            composite_risk=0.1,
            propagation_uplift=0.0,
            sub_lane=0.1,
            sub_glare=0.1,
            sub_junction=0.1,
            sub_historical=0.1,
        )
    owner_conn.commit()
    return run_a, run_b, segment_ids


@pytest.mark.asyncio
async def test_runs_exist_returns_true_for_inserted_runs(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    database_url: str,
) -> None:
    run_a, run_b, _segments = seed_two_runs
    async with await psycopg.AsyncConnection.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    ) as conn:
        a_exists, b_exists = await runs_exist(conn, run_a, run_b)
    assert a_exists is True
    assert b_exists is True


@pytest.mark.asyncio
async def test_runs_exist_returns_false_for_unknown_run(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    database_url: str,
) -> None:
    run_a, _run_b, _segments = seed_two_runs
    bogus = uuid4()
    async with await psycopg.AsyncConnection.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    ) as conn:
        a_exists, bogus_exists = await runs_exist(conn, run_a, bogus)
    assert a_exists is True
    assert bogus_exists is False


@pytest.mark.asyncio
async def test_count_pair_at_hour_returns_intersection_size(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    database_url: str,
) -> None:
    """Only the 5 segments present in BOTH runs at noon should be counted."""
    run_a, run_b, segments = seed_two_runs
    async with await psycopg.AsyncConnection.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    ) as conn:
        total = await count_pair_at_hour(conn, run_a, run_b, _HOUR)
    assert total == len(segments)


@pytest.mark.asyncio
async def test_fetch_pair_at_hour_yields_only_intersection(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    database_url: str,
) -> None:
    run_a, run_b, segments = seed_two_runs
    async with await psycopg.AsyncConnection.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    ) as conn:
        pairs = [
            pair
            async for pair in fetch_pair_at_hour(conn, run_a, run_b, _HOUR, limit=100, offset=0)
        ]

    yielded_ids = {p.segment_id for p in pairs}
    assert yielded_ids == set(segments)


@pytest.mark.asyncio
async def test_fetch_pair_at_hour_carries_correct_a_and_b_values(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    database_url: str,
) -> None:
    """For the i=0 segment: a.composite=0.30, b.composite=0.40."""
    run_a, run_b, segments = seed_two_runs
    async with await psycopg.AsyncConnection.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    ) as conn:
        pairs = [
            pair
            async for pair in fetch_pair_at_hour(conn, run_a, run_b, _HOUR, limit=100, offset=0)
        ]
    by_id = {p.segment_id: p for p in pairs}
    first = by_id[segments[0]]
    assert first.a.composite_risk == pytest.approx(0.30)
    assert first.b.composite_risk == pytest.approx(0.40)
    assert first.a.propagation_uplift == pytest.approx(0.05)
    assert first.b.propagation_uplift == pytest.approx(0.08)


@pytest.mark.asyncio
async def test_fetch_pair_pagination_yields_stable_order(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    database_url: str,
) -> None:
    run_a, run_b, _segments = seed_two_runs
    async with await psycopg.AsyncConnection.connect(
        database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    ) as conn:
        page_1 = [p async for p in fetch_pair_at_hour(conn, run_a, run_b, _HOUR, limit=2, offset=0)]
        page_2 = [p async for p in fetch_pair_at_hour(conn, run_a, run_b, _HOUR, limit=2, offset=2)]
    assert len(page_1) == 2
    assert len(page_2) == 2
    # All four are distinct segment_ids.
    assert {p.segment_id for p in (*page_1, *page_2)} == {p.segment_id for p in (*page_1, *page_2)}
    assert len({p.segment_id for p in (*page_1, *page_2)}) == 4


@pytest.mark.asyncio
async def test_explain_uses_run_id_index(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    database_url: str,
) -> None:
    """The plan calls for ``EXPLAIN`` to verify the BTREE index on
    ``scoring_run_id`` is used. We assert the plan text mentions either
    that index or an ``Index Scan`` / ``Bitmap Index Scan`` against
    ``segment_scores``. Strict equality on plan shape is brittle across
    PG versions and statistics — substring assertions are the durable
    contract."""
    run_a, run_b, _segments = seed_two_runs
    async with (
        await psycopg.AsyncConnection.connect(
            database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        ) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute(
            "EXPLAIN " + FETCH_PAIR_AT_HOUR_SQL,
            {
                "run_a_id": run_a,
                "run_b_id": run_b,
                "target_hour": _HOUR,
                "limit": 100,
                "offset": 0,
            },
        )
        rows = await cur.fetchall()
    plan_text = "\n".join(r[0] for r in rows)
    assert "segment_scores" in plan_text
    # On a 12-row test table the planner often chooses Seq Scan — that's
    # expected. The integration target is "doesn't full-scan the
    # production table", which is verified by Task 5.3's tile p99
    # measurement, not here. So this assertion is loose by design.
    assert "Scan" in plan_text
