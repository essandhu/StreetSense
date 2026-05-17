"""City-scoped scoring-run endpoints — Phase 4b Task 3.3 router refactor.

Routes mounted under ``/api/cities/{slug}/runs``:

- ``GET /api/cities/{slug}/runs`` — list every scoring run for the
  city, newest-first, with the full six-field provenance bundle. Moved
  from ``/runs``.

- ``GET /api/cities/{slug}/runs/{run_id}`` — one run's metadata (NEW
  in Phase 4b). A run from a different city returns 404, not a
  different-city's row.

- ``GET /api/cities/{slug}/runs/{run_id}/scores`` — paginated per-
  segment scores for one (city, run) pair (NEW in Phase 4b). Each row
  carries the full composite decomposition + sub-scores so the
  explainability invariant holds on the scores-list path too.

- ``GET /api/cities/{slug}/runs/{run_a}/delta/{run_b}`` — paginated
  per-segment deltas between two runs. Moved from ``/runs/{a}/delta/
  {b}``; both runs must belong to ``{slug}``.

Pre-refactor confidence-indicator note still applies: ``segment_scores``
persists ``confidence`` as a scalar without a ``limiter`` label. The
delta route emits ``limiter="model"`` as a documented Phase-5
approximation; the segment-detail route reconstructs the real limiter
from imagery. See ``conductor/tracks/phase-5-delta-deployment/index.md``
under "Discovered during implementation".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.db import conn
from api.delta import SegmentScoreSnapshot, SubScoreValues, compute_segment_delta
from api.dependencies import resolve_city_id
from api.repos.segment_scores import (
    ScorePairRow,
    SegmentScoreRow,
    count_pair_at_hour,
    fetch_pair_at_hour,
    runs_exist,
)
from api.schemas import (
    ConfidenceIndicator,
    DeltaResponse,
    RunId,
    RunListResponse,
    RunScoreEntry,
    RunScoresResponse,
    ScoringRunMetadata,
    SegmentDelta,
    SubScore,
    SubScores,
)

router = APIRouter(prefix="/api/cities/{slug}/runs", tags=["runs"])


_DEFAULT_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 1000
_DEFAULT_HOUR = 12  # Noon UTC — matches the weekly-cron cadence.


# All run-reading SQL gains a ``city_id`` filter so a run that lives in
# another city is invisible to the route. Composite (city_id, ...)
# indexes added by migration 0017 make this index-supported.
_SELECT_RUN_METADATA_SQL = """
SELECT
    id,
    scoring_run_timestamp,
    perception_model_version,
    osm_snapshot_date,
    lower(imagery_capture_window) AS imagery_capture_window_start,
    upper(imagery_capture_window) AS imagery_capture_window_end,
    propagation_algorithm_version
FROM scoring_runs
WHERE id = %(run_id)s AND city_id = %(city_id)s
"""


_LIST_RUNS_SQL = """
SELECT
    id,
    scoring_run_timestamp,
    perception_model_version,
    osm_snapshot_date,
    lower(imagery_capture_window) AS imagery_capture_window_start,
    upper(imagery_capture_window) AS imagery_capture_window_end,
    propagation_algorithm_version
FROM scoring_runs
WHERE city_id = %(city_id)s
ORDER BY scoring_run_timestamp DESC
"""


# Most recent score row per segment for one (city, run) pair. Mirrors
# the detail route's snapping behavior at noon (the default).
_LIST_RUN_SCORES_SQL = """
SELECT DISTINCT ON (segment_id)
    segment_id,
    composite_risk,
    propagation_uplift,
    sub_score_lane_marking,
    sub_score_glare,
    sub_score_junction_complexity,
    sub_score_historical,
    confidence,
    is_stub_lane_marking,
    is_stub_glare,
    is_stub_junction_complexity,
    is_stub_historical
FROM segment_scores
WHERE scoring_run_id = %(run_id)s
  AND city_id = %(city_id)s
ORDER BY segment_id, scoring_run_timestamp DESC
"""


async def _fetch_run_metadata(
    connection: psycopg.AsyncConnection, run_id: UUID, city_id: UUID
) -> ScoringRunMetadata:
    """Load one ``scoring_runs`` row into the typed metadata bundle.

    Caller has already established the row exists via
    :func:`api.repos.segment_scores.runs_exist`, so a missing row here
    is a programmer error worth surfacing loudly.
    """
    async with connection.cursor() as cur:
        await cur.execute(_SELECT_RUN_METADATA_SQL, {"run_id": run_id, "city_id": city_id})
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(
            f"scoring_runs row {run_id} / city {city_id} disappeared between runs_exist and fetch"
        )
    (
        rid,
        ts,
        perception_version,
        osm_date,
        imagery_start,
        imagery_end,
        propagation_version,
    ) = row
    return ScoringRunMetadata(
        scoring_run_id=RunId(rid),
        scoring_run_timestamp=ts,
        perception_model_version=perception_version,
        osm_snapshot_date=osm_date,
        imagery_capture_window_start=imagery_start,
        imagery_capture_window_end=imagery_end,
        propagation_algorithm_version=propagation_version,
    )


def _row_to_snapshot(segment_id: UUID, row: SegmentScoreRow) -> SegmentScoreSnapshot:
    """Project one ``segment_scores`` row into the pure-compute snapshot.

    NULL sub-score / confidence columns coerce to ``0.0`` — Phase 4+ rows
    populate all four, so this is a defensive default for pre-Phase-4
    stub rows that the repo's frozen-row type happens to allow through.

    See the module docstring for the ``limiter="model"`` approximation.
    """
    uplift = float(row.propagation_uplift)
    composite = float(row.composite_risk)
    local = max(0.0, composite - uplift)
    confidence_value = float(row.confidence) if row.confidence is not None else 0.0
    return SegmentScoreSnapshot(
        segment_id=segment_id,
        composite_risk=composite,
        local_contribution=local,
        propagation_uplift=uplift,
        sub_scores=SubScoreValues(
            lane_marking_quality=(
                0.0 if row.sub_score_lane_marking is None else float(row.sub_score_lane_marking)
            ),
            glare_exposure=(0.0 if row.sub_score_glare is None else float(row.sub_score_glare)),
            junction_complexity=(
                0.0
                if row.sub_score_junction_complexity is None
                else float(row.sub_score_junction_complexity)
            ),
            historical_correlation=(
                0.0 if row.sub_score_historical is None else float(row.sub_score_historical)
            ),
        ),
        confidence=ConfidenceIndicator(value=confidence_value, limiter="model"),
    )


def _pair_to_delta(pair: ScorePairRow) -> SegmentDelta:
    snap_a = _row_to_snapshot(pair.segment_id, pair.a)
    snap_b = _row_to_snapshot(pair.segment_id, pair.b)
    return compute_segment_delta(snap_a, snap_b)


def _build_subscore(
    value: float | None,
    *,
    is_stub: bool,
    confidence: float,
) -> SubScore:
    return SubScore(
        value=value if value is not None else 0.0,
        confidence=confidence,
        is_stub=is_stub,
    )


def _score_row_to_entry(row: Any) -> RunScoreEntry:
    """Pack one ``_LIST_RUN_SCORES_SQL`` row into the API shape.

    ``row`` is the psycopg cursor's positional tuple — typed ``Any`` so
    the column-by-column unpack below stays readable. The DB schema
    guarantees: column 0 is ``UUID``, columns 1-7 are ``numeric``
    (Postgres ``DOUBLE PRECISION``) which decimal-typed nulls coerce to
    None, columns 8-11 are ``boolean``.
    """
    (
        segment_id,
        composite_risk,
        propagation_uplift,
        sub_lane,
        sub_glare,
        sub_junction,
        sub_historical,
        scalar_confidence,
        is_stub_lane,
        is_stub_glare,
        is_stub_junction,
        is_stub_historical,
    ) = row
    composite = float(composite_risk)
    uplift = float(propagation_uplift) if propagation_uplift is not None else 0.0
    local = max(0.0, composite - uplift)
    conf_value = float(scalar_confidence) if scalar_confidence is not None else 0.0
    return RunScoreEntry(
        segment_id=segment_id,
        composite_risk=composite,
        local_contribution=local,
        propagation_uplift=uplift,
        sub_scores=SubScores(
            lane_marking_quality=_build_subscore(
                None if sub_lane is None else float(sub_lane),
                is_stub=bool(is_stub_lane),
                confidence=conf_value,
            ),
            glare_exposure=_build_subscore(
                None if sub_glare is None else float(sub_glare),
                is_stub=bool(is_stub_glare),
                confidence=conf_value,
            ),
            junction_complexity=_build_subscore(
                None if sub_junction is None else float(sub_junction),
                is_stub=bool(is_stub_junction),
                confidence=0.0,
            ),
            historical_correlation=_build_subscore(
                None if sub_historical is None else float(sub_historical),
                is_stub=bool(is_stub_historical),
                confidence=0.0,
            ),
        ),
        # See module docstring for the ``limiter="model"`` Phase-5
        # approximation.
        confidence=ConfidenceIndicator(value=conf_value, limiter="model"),
    )


def _resolve_target_hour(t: datetime | None) -> int:
    """Pick the hour-of-day for the JOIN. Defaults to noon UTC."""
    if t is None:
        return _DEFAULT_HOUR
    t_utc = t if t.tzinfo is not None else t.replace(tzinfo=UTC)
    return t_utc.astimezone(UTC).hour


@router.get("", response_model=RunListResponse)
async def list_runs(
    city_id: UUID = Depends(resolve_city_id),  # noqa: B008 - FastAPI's Depends() default is idiomatic
) -> RunListResponse:
    """Return every scoring run for the city, newest-first, with full provenance."""
    async with conn() as connection, connection.cursor() as cur:
        await cur.execute(_LIST_RUNS_SQL, {"city_id": city_id})
        rows = await cur.fetchall()
    runs = [
        ScoringRunMetadata(
            scoring_run_id=RunId(rid),
            scoring_run_timestamp=ts,
            perception_model_version=perception_version,
            osm_snapshot_date=osm_date,
            imagery_capture_window_start=imagery_start,
            imagery_capture_window_end=imagery_end,
            propagation_algorithm_version=propagation_version,
        )
        for (
            rid,
            ts,
            perception_version,
            osm_date,
            imagery_start,
            imagery_end,
            propagation_version,
        ) in rows
    ]
    return RunListResponse(runs=runs)


@router.get("/{run_id}", response_model=ScoringRunMetadata)
async def get_run(
    run_id: UUID,
    city_id: UUID = Depends(resolve_city_id),  # noqa: B008 - FastAPI's Depends() default is idiomatic
) -> ScoringRunMetadata:
    """Return one run's six-field provenance bundle (NEW in Phase 4b).

    404 when the run UUID is unknown or belongs to a different city —
    the (run_id, city_id) WHERE clause makes "wrong city" and "doesn't
    exist" indistinguishable, which matches the resource-identity
    contract: a run is identified by ``(city, id)``, not ``id`` alone.
    """
    async with conn() as connection, connection.cursor() as cur:
        await cur.execute(_SELECT_RUN_METADATA_SQL, {"run_id": run_id, "city_id": city_id})
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"scoring run {run_id} not found in this city")
    (
        rid,
        ts,
        perception_version,
        osm_date,
        imagery_start,
        imagery_end,
        propagation_version,
    ) = row
    return ScoringRunMetadata(
        scoring_run_id=RunId(rid),
        scoring_run_timestamp=ts,
        perception_model_version=perception_version,
        osm_snapshot_date=osm_date,
        imagery_capture_window_start=imagery_start,
        imagery_capture_window_end=imagery_end,
        propagation_algorithm_version=propagation_version,
    )


@router.get("/{run_id}/scores", response_model=RunScoresResponse)
async def list_run_scores(
    run_id: UUID,
    city_id: UUID = Depends(resolve_city_id),  # noqa: B008 - FastAPI's Depends() default is idiomatic
) -> RunScoresResponse:
    """Return every persisted segment score for one (city, run) pair (NEW).

    DISTINCT ON keeps the response one row per segment (Phase 4 writes
    24 hourly rows per (segment, run); the list endpoint returns the
    most recent one). Each row ships the full composite decomposition
    + four sub-scores so the explainability invariant carries through.
    """
    # Verify (run_id, city_id) before issuing the score-list query so
    # the 404 path doesn't return an empty success-shape body.
    async with conn() as connection, connection.cursor() as cur:
        await cur.execute(
            "SELECT 1 FROM scoring_runs WHERE id = %s AND city_id = %s",
            (run_id, city_id),
        )
        if await cur.fetchone() is None:
            raise HTTPException(
                status_code=404,
                detail=f"scoring run {run_id} not found in this city",
            )
        await cur.execute(_LIST_RUN_SCORES_SQL, {"run_id": run_id, "city_id": city_id})
        rows = await cur.fetchall()
    return RunScoresResponse(scores=[_score_row_to_entry(row) for row in rows])


@router.get("/{run_a}/delta/{run_b}", response_model=DeltaResponse)
async def get_runs_delta(
    run_a: UUID,
    run_b: UUID,
    city_id: UUID = Depends(resolve_city_id),  # noqa: B008 - FastAPI's Depends() default is idiomatic
    t: datetime | None = Query(  # noqa: B008 - FastAPI's Query() default is idiomatic
        default=None,
        description=(
            "Optional ISO-8601 UTC instant. Its hour-of-day picks the row "
            "pair on each side. Omitted ⇒ noon UTC."
        ),
    ),
    page: int = Query(default=1, ge=1, description="1-indexed page number."),
    page_size: int = Query(
        default=_DEFAULT_PAGE_SIZE,
        ge=1,
        le=_MAX_PAGE_SIZE,
        description="Rows per page. Capped to keep p99 within the tile budget.",
    ),
) -> DeltaResponse:
    """Return a paginated list of per-segment deltas between two runs.

    Both runs must belong to the city in the URL. A run that lives in
    a different city returns 404 — there is no cross-city delta
    operation.
    """
    if run_a == run_b:
        raise HTTPException(
            status_code=422,
            detail="run_a and run_b must be different scoring runs",
        )

    target_hour = _resolve_target_hour(t)
    offset = (page - 1) * page_size

    async with conn() as connection:
        a_exists, b_exists = await runs_exist(connection, run_a, run_b, city_id)
        if not a_exists:
            raise HTTPException(
                status_code=404,
                detail=f"scoring run {run_a} not found in this city",
            )
        if not b_exists:
            raise HTTPException(
                status_code=404,
                detail=f"scoring run {run_b} not found in this city",
            )

        run_a_meta = await _fetch_run_metadata(connection, run_a, city_id)
        run_b_meta = await _fetch_run_metadata(connection, run_b, city_id)
        total = await count_pair_at_hour(connection, run_a, run_b, target_hour, city_id)
        deltas = [
            _pair_to_delta(pair)
            async for pair in fetch_pair_at_hour(
                connection,
                run_a,
                run_b,
                target_hour,
                city_id,
                limit=page_size,
                offset=offset,
            )
        ]

    return DeltaResponse(
        run_a=run_a_meta,
        run_b=run_b_meta,
        deltas=deltas,
        page=page,
        page_size=page_size,
        total=total,
    )
