"""City-scoped API routes — Phase 4b Task 3.1 (TDD red phase).

This file is the failing-tests-first specification for the Phase 3 of
Phase 4b — the API refactor that mounts every read endpoint under
``/api/cities/{slug}/...``. Task 3.3 ships the router refactor that
turns these from red to green.

The test surface covers four invariants the implementation must
satisfy:

1. **Unknown slug → 404 with valid slugs listed.** Every city-scoped
   route resolves the slug against the ``cities`` table as the first
   thing it does. A bogus slug returns 404 with a JSON body that lists
   the slugs the caller could have used. Generic FastAPI 404s with
   ``{"detail": "Not Found"}`` are not enough — the body must surface
   the valid options so a frontend or curl user can recover without
   re-reading the docs.

2. **Sub-score decomposition asserted on every composite-risk-bearing
   response.** CLAUDE.md §"Explainability" forbids collapsing composite
   risk to a single opaque number anywhere in the stack. Detail, list,
   per-run-scores, and delta responses all return the four sub-score
   fields (`lane_marking_quality`, `glare_exposure`,
   `junction_complexity`, `historical_correlation`) — and the delta
   shape carries them as `sub_score_deltas` plus
   `local_contribution_delta` + `propagation_uplift_delta`.

3. **City scoping is enforced at every route.** A segment that lives in
   cambridge does not appear in `/api/cities/phoenix/segments`; a
   cambridge run does not appear in `/api/cities/phoenix/runs`; a
   `/api/cities/{wrong_slug}/segments/{cambridge_segment_id}` request
   returns 404, not the cambridge row.

4. **Hard cut on the legacy routes.** Per the user-confirmed cutover
   strategy, the pre-refactor unprefixed paths (``/segments/{id}``,
   ``/runs``, ``/runs/{a}/delta/{b}``) return 404 once Task 3.3 lands.
   The spec wording ("every read endpoint is namespaced under a city
   slug") rules out 308 redirects; this is a single-source-of-truth API.

Cambridge is the city with real fixture data (it's the grandfathered
demo city — ADR 0010). Phoenix is used as the second-city negative
case: phoenix has a row in the ``cities`` table (seeded by
``make seed-cities``) but no segments / runs / scores yet (Phase 2
tasks 2.5 + 2.6 were deferred). That asymmetry is exactly what the
404 / empty-list / cross-city-isolation tests need.

Integration tests — requires a running, migrated Postgres.
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

from ingestion.seed_cities import seed_cities

pytestmark = pytest.mark.integration


_PERCEPTION_VERSION = "lane-marking-standin-deadbeef"
_PROPAGATION_VERSION = "pagerank-diffusion-0.1.0"
_OSM_SNAPSHOT_DATE = date(2026, 5, 13)
_IMAGERY_START = date(2025, 11, 1)
_IMAGERY_END = date(2026, 5, 1)

# Two runs at the same hour-of-day (noon UTC) — the delta endpoint
# joins by hour, so both runs must share an hour to produce non-empty
# delta rows.
_RUN_OLD_TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
_RUN_NEW_TS = datetime(2026, 5, 8, 12, 0, 0, tzinfo=UTC)


# --- Fixtures -------------------------------------------------------------


@pytest.fixture(scope="session")
def _seeded_cities(database_url: str) -> None:
    """Ensure every YAML-configured city is in the ``cities`` table.

    Migration 0017 seeds only cambridge; phoenix / san-francisco /
    austin / los-angeles arrive via ``make seed-cities``. The Phase 3
    tests rely on phoenix being present so they can exercise the
    "valid slug, no data" branch and the cross-city-isolation
    assertions. Idempotent — re-running with no YAML changes is a no-op
    (see :class:`ingestion.seed_cities.SeedSummary`).
    """
    seed_cities(database_url)


@pytest.fixture
def phoenix_city_id(_seeded_cities: None, migrated_db: str) -> UUID:
    """Resolve phoenix's ``city_id``. Companion to the existing
    ``cambridge_city_id`` fixture in ``tests/conftest.py``."""
    del _seeded_cities
    with psycopg.connect(migrated_db) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM cities WHERE slug = 'phoenix'")
        row = cur.fetchone()
    assert row is not None, (
        "phoenix bootstrap row missing — `make seed-cities` should have seeded it"
    )
    return UUID(str(row[0]))


@pytest.fixture
def _clean_score_tables(owner_conn: psycopg.Connection[Any]) -> None:
    """Wipe the score / segment tables. Does not touch ``cities``."""
    with owner_conn.cursor() as cur:
        cur.execute(
            "TRUNCATE segment_scores, scoring_runs, segment_imagery, road_segments CASCADE"
        )
    owner_conn.commit()


def _insert_segment(
    cur: psycopg.Cursor[Any],
    *,
    osm_way_id: int,
    city_id: Any,
    coords: list[tuple[float, float]],
    attrs: str = '{"highway": "primary", "lanes": "2"}',
) -> UUID:
    geom = LineString(coords)
    cur.execute(
        """
        INSERT INTO road_segments (osm_way_id, geometry, attrs, city_id)
        VALUES (%s, ST_SetSRID(ST_GeomFromWKB(%s), 4326), %s::jsonb, %s)
        RETURNING id
        """,
        (osm_way_id, wkb.dumps(geom), attrs, city_id),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _insert_scoring_run(
    cur: psycopg.Cursor[Any],
    *,
    city_id: Any,
    run_timestamp: datetime,
    notes: str,
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
        ) VALUES (
            %s, %s, %s, daterange(%s, %s, '[)'),
            %s, %s, %s
        ) RETURNING id
        """,
        (
            run_timestamp,
            _PERCEPTION_VERSION,
            _OSM_SNAPSHOT_DATE,
            _IMAGERY_START,
            _IMAGERY_END,
            _PROPAGATION_VERSION,
            notes,
            city_id,
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


def _insert_segment_score(
    cur: psycopg.Cursor[Any],
    *,
    segment_id: UUID,
    run_id: UUID,
    run_timestamp: datetime,
    city_id: Any,
    composite: float,
    uplift: float,
) -> None:
    cur.execute(
        """
        INSERT INTO segment_scores (
            segment_id, composite_risk, propagation_uplift,
            sub_score_lane_marking, sub_score_glare,
            sub_score_junction_complexity, sub_score_historical,
            confidence,
            is_stub_lane_marking, is_stub_glare,
            is_stub_junction_complexity, is_stub_historical,
            scoring_run_id, scoring_run_timestamp,
            perception_model_version, osm_snapshot_date,
            imagery_capture_window, propagation_algorithm_version,
            city_id
        ) VALUES (
            %s, %s, %s,
            0.55, 0.30, 0.50, 0.20,
            0.8,
            false, false, false, false,
            %s, %s,
            %s, %s,
            daterange(%s, %s, '[)'), %s,
            %s
        )
        """,
        (
            segment_id,
            composite,
            uplift,
            run_id,
            run_timestamp,
            _PERCEPTION_VERSION,
            _OSM_SNAPSHOT_DATE,
            _IMAGERY_START,
            _IMAGERY_END,
            _PROPAGATION_VERSION,
            city_id,
        ),
    )


@pytest.fixture
def seed_cambridge_data(
    _clean_score_tables: None,
    owner_conn: psycopg.Connection[Any],
    cambridge_city_id: Any,
) -> dict[str, Any]:
    """Insert a small but complete cambridge dataset.

    Returns a dict carrying the IDs used by the test cases so the
    body of each test can stay focused on the API contract rather
    than DB plumbing.
    """
    del _clean_score_tables
    with owner_conn.cursor() as cur:
        seg_a = _insert_segment(
            cur,
            osm_way_id=910_001,
            city_id=cambridge_city_id,
            coords=[(-71.110, 42.370), (-71.100, 42.370)],
        )
        seg_b = _insert_segment(
            cur,
            osm_way_id=910_002,
            city_id=cambridge_city_id,
            coords=[(-71.105, 42.380), (-71.095, 42.380)],
        )
        run_old = _insert_scoring_run(
            cur,
            city_id=cambridge_city_id,
            run_timestamp=_RUN_OLD_TS,
            notes="phase-4b task 3.1 — cambridge old",
        )
        run_new = _insert_scoring_run(
            cur,
            city_id=cambridge_city_id,
            run_timestamp=_RUN_NEW_TS,
            notes="phase-4b task 3.1 — cambridge new",
        )
        for run_id, run_ts, composite, uplift in (
            (run_old, _RUN_OLD_TS, 0.55, 0.10),
            (run_new, _RUN_NEW_TS, 0.65, 0.15),
        ):
            for seg in (seg_a, seg_b):
                _insert_segment_score(
                    cur,
                    segment_id=seg,
                    run_id=run_id,
                    run_timestamp=run_ts,
                    city_id=cambridge_city_id,
                    composite=composite,
                    uplift=uplift,
                )
    owner_conn.commit()
    return {
        "segment_a": seg_a,
        "segment_b": seg_b,
        "run_old": run_old,
        "run_new": run_new,
    }


@pytest.fixture
def seed_phoenix_data(
    seed_cambridge_data: dict[str, Any],
    owner_conn: psycopg.Connection[Any],
    phoenix_city_id: UUID,
) -> dict[str, Any]:
    """Add a phoenix segment + run on top of the cambridge dataset.

    Lets the city-scoping tests assert that a cambridge query does
    not return phoenix rows (and vice versa).
    """
    with owner_conn.cursor() as cur:
        seg_phx = _insert_segment(
            cur,
            osm_way_id=920_001,
            city_id=phoenix_city_id,
            coords=[(-112.10, 33.50), (-112.09, 33.50)],
        )
        run_phx = _insert_scoring_run(
            cur,
            city_id=phoenix_city_id,
            run_timestamp=_RUN_NEW_TS,
            notes="phase-4b task 3.1 — phoenix",
        )
        _insert_segment_score(
            cur,
            segment_id=seg_phx,
            run_id=run_phx,
            run_timestamp=_RUN_NEW_TS,
            city_id=phoenix_city_id,
            composite=0.72,
            uplift=0.20,
        )
    owner_conn.commit()
    return {
        **seed_cambridge_data,
        "phoenix_segment": seg_phx,
        "phoenix_run": run_phx,
    }


# --- Helpers --------------------------------------------------------------


def _assert_sub_score_decomposition(sub_scores: dict[str, Any]) -> None:
    """Assert all four sub-score fields are present.

    The explainability invariant: every composite-risk-bearing response
    carries the four sub-scores. Each entry is itself an object with
    at minimum a numeric ``value`` and a boolean ``is_stub``.
    """
    assert set(sub_scores.keys()) == {
        "lane_marking_quality",
        "glare_exposure",
        "junction_complexity",
        "historical_correlation",
    }, f"missing sub-score field(s); got {sorted(sub_scores.keys())}"
    for name, entry in sub_scores.items():
        assert "value" in entry, f"{name}: value missing"
        assert "is_stub" in entry, f"{name}: is_stub missing"


def _assert_sub_score_deltas(sub_score_deltas: dict[str, Any]) -> None:
    """Same explainability invariant adapted for the delta shape."""
    assert set(sub_score_deltas.keys()) == {
        "lane_marking_quality",
        "glare_exposure",
        "junction_complexity",
        "historical_correlation",
    }, f"missing sub-score delta field(s); got {sorted(sub_score_deltas.keys())}"
    for name, value in sub_score_deltas.items():
        assert isinstance(value, (int, float)), f"sub_score_deltas.{name} is not numeric"


# --- Unknown-slug 404s ----------------------------------------------------


class TestUnknownSlugReturns404WithValidSlugsListed:
    """Every city-scoped route resolves the slug first; bogus → 404.

    The 404 body MUST list the valid slugs so callers can recover
    without re-reading docs. A bare ``{"detail": "Not Found"}`` from
    FastAPI's default 404 handler is insufficient.
    """

    @pytest.mark.asyncio
    async def test_segments_list_unknown_slug(
        self,
        _seeded_cities: None,
        api_client: AsyncClient,
    ) -> None:
        del _seeded_cities
        resp = await api_client.get("/api/cities/atlantis/segments", params={"limit": 5})
        assert resp.status_code == 404
        body = resp.json()
        assert "valid_slugs" in body, f"404 body must list valid_slugs; got {body!r}"
        valid = body["valid_slugs"]
        assert isinstance(valid, list) and len(valid) >= 1
        assert "cambridge" in valid

    @pytest.mark.asyncio
    async def test_segment_detail_unknown_slug(
        self,
        _seeded_cities: None,
        api_client: AsyncClient,
    ) -> None:
        del _seeded_cities
        resp = await api_client.get(f"/api/cities/atlantis/segments/{uuid4()}")
        assert resp.status_code == 404
        body = resp.json()
        assert "valid_slugs" in body
        assert "cambridge" in body["valid_slugs"]

    @pytest.mark.asyncio
    async def test_runs_list_unknown_slug(
        self,
        _seeded_cities: None,
        api_client: AsyncClient,
    ) -> None:
        del _seeded_cities
        resp = await api_client.get("/api/cities/atlantis/runs")
        assert resp.status_code == 404
        body = resp.json()
        assert "valid_slugs" in body
        assert "cambridge" in body["valid_slugs"]

    @pytest.mark.asyncio
    async def test_run_detail_unknown_slug(
        self,
        _seeded_cities: None,
        api_client: AsyncClient,
    ) -> None:
        del _seeded_cities
        resp = await api_client.get(f"/api/cities/atlantis/runs/{uuid4()}")
        assert resp.status_code == 404
        body = resp.json()
        assert "valid_slugs" in body
        assert "cambridge" in body["valid_slugs"]

    @pytest.mark.asyncio
    async def test_run_scores_unknown_slug(
        self,
        _seeded_cities: None,
        api_client: AsyncClient,
    ) -> None:
        del _seeded_cities
        resp = await api_client.get(f"/api/cities/atlantis/runs/{uuid4()}/scores")
        assert resp.status_code == 404
        body = resp.json()
        assert "valid_slugs" in body
        assert "cambridge" in body["valid_slugs"]

    @pytest.mark.asyncio
    async def test_runs_delta_unknown_slug(
        self,
        _seeded_cities: None,
        api_client: AsyncClient,
    ) -> None:
        del _seeded_cities
        resp = await api_client.get(
            f"/api/cities/atlantis/runs/{uuid4()}/delta/{uuid4()}"
        )
        assert resp.status_code == 404
        body = resp.json()
        assert "valid_slugs" in body
        assert "cambridge" in body["valid_slugs"]


# --- Segment detail under city prefix -------------------------------------


class TestSegmentDetailUnderCityPrefix:
    """``GET /api/cities/{slug}/segments/{id}`` — moved from ``/segments/{id}``."""

    @pytest.mark.asyncio
    async def test_returns_200_with_sub_score_decomposition(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        seg_id = seed_cambridge_data["segment_a"]
        resp = await api_client.get(f"/api/cities/cambridge/segments/{seg_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Composite decomposition (CLAUDE.md §"Explainability").
        assert "composite_risk" in body
        assert "local_contribution" in body
        assert "propagation_uplift" in body
        _assert_sub_score_decomposition(body["sub_scores"])
        # Confidence indicator preserved.
        assert "confidence" in body
        assert "value" in body["confidence"]
        assert "limiter" in body["confidence"]

    @pytest.mark.asyncio
    async def test_returns_404_when_segment_belongs_to_a_different_city(
        self,
        seed_phoenix_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        """Cambridge segment requested under the phoenix slug → 404.

        The segment exists; the slug exists; what doesn't exist is the
        pairing. The contract is "this segment doesn't belong to this
        city" — same surface as a missing UUID, because the city scope
        is part of the resource identity.
        """
        cambridge_seg = seed_phoenix_data["segment_a"]
        resp = await api_client.get(f"/api/cities/phoenix/segments/{cambridge_seg}")
        assert resp.status_code == 404, resp.text

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_segment_in_known_city(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        del seed_cambridge_data
        resp = await api_client.get(f"/api/cities/cambridge/segments/{uuid4()}")
        assert resp.status_code == 404


# --- Segments list endpoint (new in Phase 4b) -----------------------------


class TestSegmentsListUnderCityPrefix:
    """``GET /api/cities/{slug}/segments?limit=N`` — new list endpoint.

    The verification line of plan.md Phase 3 names this endpoint
    explicitly: ``curl /api/cities/phoenix/segments?limit=5`` must
    return non-empty results with sub-score fields. This is a new
    endpoint (Phase 1-4 only ever shipped ``/segments/{id}``).
    """

    @pytest.mark.asyncio
    async def test_returns_200_with_array_of_segments(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        del seed_cambridge_data
        resp = await api_client.get(
            "/api/cities/cambridge/segments", params={"limit": 5}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Wrapped list shape (matches RunListResponse precedent) so a
        # future pagination addition lands without breaking clients.
        assert "segments" in body
        items = body["segments"]
        assert isinstance(items, list)
        assert len(items) >= 1

    @pytest.mark.asyncio
    async def test_each_row_carries_sub_score_decomposition(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        """Explainability invariant on the list endpoint.

        Every row that surfaces ``composite_risk`` must also surface
        the sub-score decomposition. Collapsing to a single number on
        a list endpoint would be the easiest accidental violation —
        explicitly tested.
        """
        del seed_cambridge_data
        resp = await api_client.get(
            "/api/cities/cambridge/segments", params={"limit": 5}
        )
        items = resp.json()["segments"]
        for entry in items:
            assert "composite_risk" in entry
            assert "sub_scores" in entry
            _assert_sub_score_decomposition(entry["sub_scores"])

    @pytest.mark.asyncio
    async def test_list_is_city_scoped(
        self,
        seed_phoenix_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        """Cambridge segments do not leak into ``/api/cities/phoenix/segments``."""
        cambridge_ids = {
            seed_phoenix_data["segment_a"],
            seed_phoenix_data["segment_b"],
        }
        resp = await api_client.get(
            "/api/cities/phoenix/segments", params={"limit": 50}
        )
        assert resp.status_code == 200, resp.text
        returned_ids = {UUID(entry["segment_id"]) for entry in resp.json()["segments"]}
        assert returned_ids.isdisjoint(cambridge_ids), (
            f"phoenix list leaked cambridge segments: {returned_ids & cambridge_ids}"
        )

    @pytest.mark.asyncio
    async def test_list_respects_limit(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        del seed_cambridge_data
        resp = await api_client.get(
            "/api/cities/cambridge/segments", params={"limit": 1}
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["segments"]) == 1


# --- Runs list / detail / scores under city prefix ------------------------


class TestRunsListUnderCityPrefix:
    """``GET /api/cities/{slug}/runs`` — moved from ``/runs``, now city-filtered."""

    @pytest.mark.asyncio
    async def test_returns_runs_for_city_newest_first(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        resp = await api_client.get("/api/cities/cambridge/runs")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "runs" in body
        runs = body["runs"]
        assert len(runs) == 2
        # Newest first — same ordering as the legacy list.
        assert UUID(runs[0]["scoring_run_id"]) == seed_cambridge_data["run_new"]
        assert UUID(runs[1]["scoring_run_id"]) == seed_cambridge_data["run_old"]
        # Full six-field provenance bundle preserved.
        for r in runs:
            for key in (
                "scoring_run_id",
                "scoring_run_timestamp",
                "perception_model_version",
                "osm_snapshot_date",
                "imagery_capture_window_start",
                "imagery_capture_window_end",
                "propagation_algorithm_version",
            ):
                assert key in r, f"run missing provenance field {key!r}"

    @pytest.mark.asyncio
    async def test_runs_are_city_scoped(
        self,
        seed_phoenix_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        """A phoenix run does not appear in cambridge's list."""
        resp = await api_client.get("/api/cities/cambridge/runs")
        ids = {UUID(r["scoring_run_id"]) for r in resp.json()["runs"]}
        assert seed_phoenix_data["phoenix_run"] not in ids

    @pytest.mark.asyncio
    async def test_city_with_no_runs_returns_empty_list(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        """Phoenix has been seeded as a city but has no scoring runs —
        200 + empty list, not 404. The slug is valid; the data is just
        absent."""
        del seed_cambridge_data
        resp = await api_client.get("/api/cities/phoenix/runs")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"runs": []}


class TestRunDetailUnderCityPrefix:
    """``GET /api/cities/{slug}/runs/{run_id}`` — new single-run metadata route."""

    @pytest.mark.asyncio
    async def test_returns_200_with_provenance_bundle(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        run_id = seed_cambridge_data["run_new"]
        resp = await api_client.get(f"/api/cities/cambridge/runs/{run_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in (
            "scoring_run_id",
            "scoring_run_timestamp",
            "perception_model_version",
            "osm_snapshot_date",
            "imagery_capture_window_start",
            "imagery_capture_window_end",
            "propagation_algorithm_version",
        ):
            assert key in body
        assert UUID(body["scoring_run_id"]) == run_id

    @pytest.mark.asyncio
    async def test_returns_404_when_run_belongs_to_a_different_city(
        self,
        seed_phoenix_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        cambridge_run = seed_phoenix_data["run_new"]
        resp = await api_client.get(f"/api/cities/phoenix/runs/{cambridge_run}")
        assert resp.status_code == 404, resp.text

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_run_uuid(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        del seed_cambridge_data
        resp = await api_client.get(f"/api/cities/cambridge/runs/{uuid4()}")
        assert resp.status_code == 404


class TestRunScoresUnderCityPrefix:
    """``GET /api/cities/{slug}/runs/{run_id}/scores`` — new paginated score list.

    Surfaces every persisted ``segment_scores`` row for one (city,
    run) pair. The explainability invariant carries through: each
    score row ships the full sub-score decomposition.
    """

    @pytest.mark.asyncio
    async def test_returns_200_with_array_carrying_sub_score_decomposition(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        run_id = seed_cambridge_data["run_new"]
        resp = await api_client.get(f"/api/cities/cambridge/runs/{run_id}/scores")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "scores" in body
        scores = body["scores"]
        # Two segments × one run = two score rows.
        assert len(scores) == 2
        for entry in scores:
            assert "segment_id" in entry
            assert "composite_risk" in entry
            assert "local_contribution" in entry
            assert "propagation_uplift" in entry
            _assert_sub_score_decomposition(entry["sub_scores"])

    @pytest.mark.asyncio
    async def test_returns_404_when_run_belongs_to_a_different_city(
        self,
        seed_phoenix_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        cambridge_run = seed_phoenix_data["run_new"]
        resp = await api_client.get(
            f"/api/cities/phoenix/runs/{cambridge_run}/scores"
        )
        assert resp.status_code == 404, resp.text


class TestRunsDeltaUnderCityPrefix:
    """``GET /api/cities/{slug}/runs/{a}/delta/{b}`` — moved from ``/runs/{a}/delta/{b}``."""

    @pytest.mark.asyncio
    async def test_returns_200_with_sub_score_deltas(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        run_a = seed_cambridge_data["run_old"]
        run_b = seed_cambridge_data["run_new"]
        resp = await api_client.get(
            f"/api/cities/cambridge/runs/{run_a}/delta/{run_b}",
            params={"t": "2026-05-08T12:00:00+00:00"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "deltas" in body
        assert len(body["deltas"]) >= 1
        for entry in body["deltas"]:
            assert "composite_delta" in entry
            assert "local_contribution_delta" in entry
            assert "propagation_uplift_delta" in entry
            # Decomposition invariant: composite = local + uplift.
            assert entry["composite_delta"] == pytest.approx(
                entry["local_contribution_delta"] + entry["propagation_uplift_delta"],
                abs=1e-6,
            )
            assert "sub_score_deltas" in entry
            _assert_sub_score_deltas(entry["sub_score_deltas"])

    @pytest.mark.asyncio
    async def test_returns_404_when_either_run_belongs_to_a_different_city(
        self,
        seed_phoenix_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        """One cambridge run + one phoenix run requested under cambridge → 404.

        Both runs must belong to the named city — a cross-city delta
        is not a supported operation.
        """
        cambridge_run = seed_phoenix_data["run_old"]
        phoenix_run = seed_phoenix_data["phoenix_run"]
        resp = await api_client.get(
            f"/api/cities/cambridge/runs/{cambridge_run}/delta/{phoenix_run}"
        )
        assert resp.status_code == 404, resp.text


# --- Legacy routes are gone (hard cut) ------------------------------------


class TestLegacyUnprefixedRoutesHardCut:
    """Pre-Phase-4b unprefixed routes return 404 after Task 3.3.

    The user-confirmed cutover strategy: no 308 redirects, no parallel
    mounting. The legacy `/segments/{id}` and `/runs` paths are gone.
    """

    @pytest.mark.asyncio
    async def test_legacy_segment_detail_returns_404(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        seg_id = seed_cambridge_data["segment_a"]
        resp = await api_client.get(f"/segments/{seg_id}")
        assert resp.status_code == 404, resp.text

    @pytest.mark.asyncio
    async def test_legacy_runs_list_returns_404(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        del seed_cambridge_data
        resp = await api_client.get("/runs")
        assert resp.status_code == 404, resp.text

    @pytest.mark.asyncio
    async def test_legacy_runs_delta_returns_404(
        self,
        seed_cambridge_data: dict[str, Any],
        api_client: AsyncClient,
    ) -> None:
        run_a = seed_cambridge_data["run_old"]
        run_b = seed_cambridge_data["run_new"]
        resp = await api_client.get(f"/runs/{run_a}/delta/{run_b}")
        assert resp.status_code == 404, resp.text
