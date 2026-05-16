"""Integration tests for ``GET /runs/{run_a}/delta/{run_b}`` (Task 2.4).

The route returns a paginated :class:`api.schemas.DeltaResponse`. These
tests seed two ``scoring_runs`` at the same hour-of-day (noon, a week
apart — matching the planned weekly-cron cadence) with five overlapping
segments and exercise:

* **Happy path** — 200 with a well-formed body; per-row composite
  decomposition holds (``composite_delta == local_contribution_delta +
  propagation_uplift_delta``); both ``confidence_a`` and ``confidence_b``
  populated; both run-metadata bundles carry the persisted provenance.
* **404** when either ``run_a`` or ``run_b`` is unknown.
* **422** when ``run_a == run_b`` — validation must happen before any DB
  lookup so a self-delta request never even touches the DB.
* **Pagination** — ``page`` / ``page_size`` slice the list; ``total``
  reflects the full intersection size across pages.
* **Empty intersection** — two runs whose segments don't overlap return a
  well-formed empty ``deltas`` list with ``total == 0`` and both run
  metadata bundles still populated.

Skipped without ``DATABASE_URL`` — the ``api_client`` fixture in
``tests/api/conftest.py`` gates on it. Per the memory note in
``tests/README.md``, the autouse TRUNCATE fixture wipes live data, so run
these BEFORE live ingests or against a separate test database.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from httpx import AsyncClient
from shapely import wkb
from shapely.geometry import LineString

pytestmark = pytest.mark.integration


_HOUR = 12
_RUN_A_TS = datetime(2026, 5, 8, _HOUR, 0, 0, tzinfo=UTC)
_RUN_B_TS = datetime(2026, 5, 15, _HOUR, 0, 0, tzinfo=UTC)
_OSM_SNAPSHOT_DATE = date(2026, 5, 1)
_IMAGERY_START = date(2025, 11, 1)
_IMAGERY_END = date(2026, 5, 1)
_PERCEPTION_VERSION = "stand-in-onnx-0.1.0"
_PROPAGATION_VERSION = "pagerank-diffusion-0.1.0"


def _insert_scoring_run(cur: psycopg.Cursor[Any], run_timestamp: datetime, *, notes: str) -> UUID:
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


def _insert_segment(cur: psycopg.Cursor[Any], offset_idx: int) -> UUID:
    base_lon = -71.10 - (offset_idx * 0.001)
    base_lat = 42.36 + (offset_idx * 0.001)
    geom = LineString([(base_lon, base_lat), (base_lon + 0.001, base_lat + 0.001)])
    cur.execute(
        """
        INSERT INTO road_segments (osm_way_id, geometry, attrs)
        VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb)
        RETURNING id
        """,
        (
            910_000 + offset_idx,
            wkb.dumps(geom),
            '{"highway": "primary"}',
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
            is_stub_historical
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, daterange(%s, %s, '[)'),
            %s, %s,
            false, false, false, false
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
            _PERCEPTION_VERSION,
            _OSM_SNAPSHOT_DATE,
            _IMAGERY_START,
            _IMAGERY_END,
            _PROPAGATION_VERSION,
            propagation_uplift,
        ),
    )


@pytest.fixture
def seed_two_runs(owner_conn: psycopg.Connection[Any]) -> tuple[UUID, UUID, list[UUID]]:
    """Two ``scoring_runs`` at noon a week apart with five overlapping segments.

    Both runs also seed a non-overlapping segment each, so a correct
    JOIN drops them. Returns ``(run_a_id, run_b_id, [overlapping_segment_ids])``.
    """
    n_segments = 5
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, segment_imagery, road_segments CASCADE")
        run_a = _insert_scoring_run(cur, _RUN_A_TS, notes="task 2.4 delta route — A")
        run_b = _insert_scoring_run(cur, _RUN_B_TS, notes="task 2.4 delta route — B")
        segment_ids: list[UUID] = []
        for i in range(n_segments):
            sid = _insert_segment(cur, i)
            segment_ids.append(sid)
            _insert_score(
                cur,
                sid,
                run_a,
                _RUN_A_TS,
                composite_risk=0.30 + i * 0.05,
                propagation_uplift=0.05 + i * 0.01,
                sub_lane=0.20 + i * 0.05,
                sub_glare=0.10 + i * 0.02,
                sub_junction=0.30,
                sub_historical=0.15,
                confidence=0.70,
            )
            _insert_score(
                cur,
                sid,
                run_b,
                _RUN_B_TS,
                composite_risk=0.40 + i * 0.05,  # +0.10 from run_a
                propagation_uplift=0.08 + i * 0.01,  # +0.03
                sub_lane=0.25 + i * 0.05,  # +0.05
                sub_glare=0.08 + i * 0.02,  # -0.02
                sub_junction=0.30,
                sub_historical=0.15,
                confidence=0.90,
            )
        # Non-overlapping segments — must NOT appear in deltas.
        only_a = _insert_segment(cur, 100)
        _insert_score(
            cur,
            only_a,
            run_a,
            _RUN_A_TS,
            composite_risk=0.1,
            propagation_uplift=0.0,
            sub_lane=0.1,
            sub_glare=0.1,
            sub_junction=0.1,
            sub_historical=0.1,
        )
        only_b = _insert_segment(cur, 101)
        _insert_score(
            cur,
            only_b,
            run_b,
            _RUN_B_TS,
            composite_risk=0.1,
            propagation_uplift=0.0,
            sub_lane=0.1,
            sub_glare=0.1,
            sub_junction=0.1,
            sub_historical=0.1,
        )
    owner_conn.commit()
    return run_a, run_b, segment_ids


@pytest.fixture
def seed_two_runs_no_overlap(
    owner_conn: psycopg.Connection[Any],
) -> tuple[UUID, UUID]:
    """Two ``scoring_runs`` at noon with disjoint segment sets.

    The intersection is empty — the route must still return a well-formed
    response with empty ``deltas`` and ``total == 0``.
    """
    with owner_conn.cursor() as cur:
        cur.execute("TRUNCATE segment_scores, scoring_runs, segment_imagery, road_segments CASCADE")
        run_a = _insert_scoring_run(cur, _RUN_A_TS, notes="task 2.4 empty — A")
        run_b = _insert_scoring_run(cur, _RUN_B_TS, notes="task 2.4 empty — B")
        a_sid = _insert_segment(cur, 50)
        _insert_score(
            cur,
            a_sid,
            run_a,
            _RUN_A_TS,
            composite_risk=0.2,
            propagation_uplift=0.0,
            sub_lane=0.1,
            sub_glare=0.1,
            sub_junction=0.1,
            sub_historical=0.1,
        )
        b_sid = _insert_segment(cur, 51)
        _insert_score(
            cur,
            b_sid,
            run_b,
            _RUN_B_TS,
            composite_risk=0.3,
            propagation_uplift=0.0,
            sub_lane=0.1,
            sub_glare=0.1,
            sub_junction=0.1,
            sub_historical=0.1,
        )
    owner_conn.commit()
    return run_a, run_b


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delta_happy_path_returns_200_with_well_formed_body(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    api_client: AsyncClient,
) -> None:
    """Two seeded runs → 200 with a well-formed DeltaResponse body."""
    run_a, run_b, segments = seed_two_runs
    response = await api_client.get(
        f"/runs/{run_a}/delta/{run_b}",
        params={"t": _RUN_B_TS.isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Top-level envelope.
    assert "run_a" in body
    assert "run_b" in body
    assert "deltas" in body
    assert "page" in body
    assert "page_size" in body
    assert "total" in body
    # The 5 overlapping segments yield 5 delta rows in one page.
    assert body["total"] == len(segments)
    assert len(body["deltas"]) == len(segments)


@pytest.mark.asyncio
async def test_delta_run_metadata_bundles_match_persisted_rows(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    api_client: AsyncClient,
) -> None:
    """``run_a`` and ``run_b`` metadata reflect what's in ``scoring_runs``."""
    run_a, run_b, _segments = seed_two_runs
    response = await api_client.get(
        f"/runs/{run_a}/delta/{run_b}",
        params={"t": _RUN_B_TS.isoformat()},
    )
    body = response.json()
    meta_a = body["run_a"]
    meta_b = body["run_b"]
    assert meta_a["scoring_run_id"] == str(run_a)
    assert meta_b["scoring_run_id"] == str(run_b)
    for meta in (meta_a, meta_b):
        assert meta["perception_model_version"] == _PERCEPTION_VERSION
        assert meta["propagation_algorithm_version"] == _PROPAGATION_VERSION
        assert meta["osm_snapshot_date"] == _OSM_SNAPSHOT_DATE.isoformat()
        assert meta["imagery_capture_window_start"] == _IMAGERY_START.isoformat()
        assert meta["imagery_capture_window_end"] == _IMAGERY_END.isoformat()


@pytest.mark.asyncio
async def test_delta_each_row_carries_composite_decomposition(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    api_client: AsyncClient,
) -> None:
    """Every delta row honors the explainability invariant
    (CLAUDE.md): ``composite_delta == local_contribution_delta +
    propagation_uplift_delta``."""
    run_a, run_b, _segments = seed_two_runs
    response = await api_client.get(
        f"/runs/{run_a}/delta/{run_b}",
        params={"t": _RUN_B_TS.isoformat()},
    )
    body = response.json()
    assert body["deltas"], "fixture seeded 5 overlapping segments — deltas must not be empty"
    for row in body["deltas"]:
        assert row["composite_delta"] == pytest.approx(
            row["local_contribution_delta"] + row["propagation_uplift_delta"],
            abs=1e-9,
        )


@pytest.mark.asyncio
async def test_delta_each_row_carries_both_confidence_indicators(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    api_client: AsyncClient,
) -> None:
    """Per the explainability invariant, every delta row carries
    *both* sides' confidence so the UI can label how confident each end
    of the comparison was. Limiter labels are constrained by the
    ConfidenceIndicator literal."""
    run_a, run_b, _segments = seed_two_runs
    response = await api_client.get(
        f"/runs/{run_a}/delta/{run_b}",
        params={"t": _RUN_B_TS.isoformat()},
    )
    body = response.json()
    valid_limiters = {"freshness", "coverage", "model"}
    for row in body["deltas"]:
        for side in ("confidence_a", "confidence_b"):
            assert side in row
            assert 0.0 <= row[side]["value"] <= 1.0
            assert row[side]["limiter"] in valid_limiters


@pytest.mark.asyncio
async def test_delta_values_match_persisted_differences(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    api_client: AsyncClient,
) -> None:
    """For the i=0 segment the seed inserted composite 0.30 vs 0.40 →
    composite_delta=0.10; uplift 0.05 vs 0.08 → propagation_uplift_delta=0.03.
    The decomposition pins local_contribution_delta to the remainder."""
    run_a, run_b, segments = seed_two_runs
    response = await api_client.get(
        f"/runs/{run_a}/delta/{run_b}",
        params={"t": _RUN_B_TS.isoformat()},
    )
    body = response.json()
    by_id = {row["segment_id"]: row for row in body["deltas"]}
    target = by_id[str(segments[0])]
    assert target["composite_delta"] == pytest.approx(0.10, abs=1e-6)
    assert target["propagation_uplift_delta"] == pytest.approx(0.03, abs=1e-6)
    # composite = local + uplift, so local_delta = composite_delta - uplift_delta.
    assert target["local_contribution_delta"] == pytest.approx(0.07, abs=1e-6)
    # Sub-score deltas reflect the seeded differences.
    assert target["sub_score_deltas"]["lane_marking_quality"] == pytest.approx(0.05, abs=1e-6)
    assert target["sub_score_deltas"]["glare_exposure"] == pytest.approx(-0.02, abs=1e-6)


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delta_returns_404_when_run_a_missing(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    api_client: AsyncClient,
) -> None:
    _run_a, run_b, _segments = seed_two_runs
    bogus = uuid4()
    response = await api_client.get(f"/runs/{bogus}/delta/{run_b}")
    assert response.status_code == 404, response.text
    body = response.json()
    # The unknown run ID is surfaced in the detail.
    assert str(bogus) in body["detail"]


@pytest.mark.asyncio
async def test_delta_returns_404_when_run_b_missing(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    api_client: AsyncClient,
) -> None:
    run_a, _run_b, _segments = seed_two_runs
    bogus = uuid4()
    response = await api_client.get(f"/runs/{run_a}/delta/{bogus}")
    assert response.status_code == 404, response.text
    body = response.json()
    assert str(bogus) in body["detail"]


@pytest.mark.asyncio
async def test_delta_returns_422_when_run_a_equals_run_b(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    api_client: AsyncClient,
) -> None:
    """Self-delta makes no sense — the route rejects with 422 before any
    DB lookup so a self-request never touches the JOIN."""
    run_a, _run_b, _segments = seed_two_runs
    response = await api_client.get(f"/runs/{run_a}/delta/{run_a}")
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_delta_returns_422_for_invalid_uuid_path_param(
    api_client: AsyncClient,
) -> None:
    """FastAPI's path-param coercion returns 422 for non-UUID strings —
    no DB needed."""
    response = await api_client.get("/runs/not-a-uuid/delta/also-not-a-uuid")
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delta_pagination_slices_the_list(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    api_client: AsyncClient,
) -> None:
    """With 5 overlapping segments, page_size=2 yields 2+2+1 deltas across
    three pages. ``total`` reflects the full intersection on every page."""
    run_a, run_b, segments = seed_two_runs
    t_iso = _RUN_B_TS.isoformat()
    seen: set[str] = set()
    for page in (1, 2, 3):
        response = await api_client.get(
            f"/runs/{run_a}/delta/{run_b}",
            params={"t": t_iso, "page": page, "page_size": 2},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == len(segments)
        assert body["page"] == page
        assert body["page_size"] == 2
        seen.update(row["segment_id"] for row in body["deltas"])
        if page < 3:
            assert len(body["deltas"]) == 2
        else:
            # 5 segments paged at size 2 ⇒ page 3 has 1 row.
            assert len(body["deltas"]) == 1
    assert seen == {str(sid) for sid in segments}


@pytest.mark.asyncio
async def test_delta_default_pagination_returns_first_page(
    seed_two_runs: tuple[UUID, UUID, list[UUID]],
    api_client: AsyncClient,
) -> None:
    """No pagination params → page=1, sensible default page_size, all 5
    overlapping segments fit on page 1."""
    run_a, run_b, segments = seed_two_runs
    response = await api_client.get(
        f"/runs/{run_a}/delta/{run_b}",
        params={"t": _RUN_B_TS.isoformat()},
    )
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] >= len(segments)
    assert len(body["deltas"]) == len(segments)


# ---------------------------------------------------------------------------
# Empty intersection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delta_empty_intersection_returns_empty_deltas_and_zero_total(
    seed_two_runs_no_overlap: tuple[UUID, UUID],
    api_client: AsyncClient,
) -> None:
    """Two runs with disjoint segments still produce a well-formed
    DeltaResponse — ``deltas == []``, ``total == 0``, both run metadata
    bundles populated."""
    run_a, run_b = seed_two_runs_no_overlap
    response = await api_client.get(
        f"/runs/{run_a}/delta/{run_b}",
        params={"t": _RUN_B_TS.isoformat()},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deltas"] == []
    assert body["total"] == 0
    assert body["run_a"]["scoring_run_id"] == str(run_a)
    assert body["run_b"]["scoring_run_id"] == str(run_b)
