"""``GET /runs/{run_a}/delta/{run_b}`` — paginated per-segment risk deltas.

Phase 5 Task 2.4. The route stitches together three already-built pieces:

* ``api.repos.segment_scores`` (Task 2.3) — the single self-JOIN that
  paginates score pairs at one hour-of-day.
* ``api.delta.compute_segment_delta`` (Task 2.2) — the pure-functional
  ``score_b - score_a`` reducer that preserves the composite
  decomposition (``composite_delta == local_contribution_delta +
  propagation_uplift_delta``).
* ``api.schemas`` — :class:`DeltaResponse` and friends (Task 2.1).

Validation order (cheapest checks first, so a bad request never touches
the JOIN):

1. **422** when ``run_a == run_b`` — a self-delta is meaningless.
2. **404** when either run UUID is missing from ``scoring_runs`` (per
   :func:`api.repos.segment_scores.runs_exist`).
3. **200** with the paginated body.

Confidence-indicator note (deviation from the Task 2.3 repo docstring's
sketch): ``segment_scores`` persists confidence as a *scalar*; the
matching ``limiter`` label is not stored. The single-segment detail
route reconstructs the real limiter by re-querying ``segment_imagery``
and recomputing freshness / coverage / model — viable for one segment
but N+1-heavy for a paginated list of hundreds. This route therefore
emits the recorded scalar value paired with ``limiter="model"`` as a
documented Phase-5 approximation: the value is honest (it *is* the
min-rule output computed at scoring time), but the limiter label is a
default rather than a per-row truth. Captured under "Discovered during
implementation" in ``conductor/tracks/phase-5-delta-deployment/index.md``
so a future task can either (a) persist ``confidence_limiter`` on
``segment_scores`` via a migration, or (b) batch the imagery lookup
across the page and recompute. Either is a no-op extension; today's
shape ships the right *value* and a stable label for the UI.

Time parameter: ``t`` is an optional ISO-8601 UTC instant. Its
hour-of-day picks the row pair on each side (Phase 4 writes 24 rows per
``(segment, run)`` keyed by ``scoring_run_timestamp``). When omitted,
defaults to noon UTC — matches the weekly-noon cron cadence and the
test fixtures' hour.

Plan deviation: this lives at ``api/routes/runs.py`` rather than the
``api/services/`` path the plan named, mirroring the existing flat
``api/routes/`` layout (``segments.py``, ``admin.py``). See ADR-style
note in ``conductor/tracks/phase-5-delta-deployment/index.md`` under
"Discovered during implementation" alongside the Task 2.1 / 2.2 / 2.3
notes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Query

from api.db import conn
from api.delta import SegmentScoreSnapshot, SubScoreValues, compute_segment_delta
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
    ScoringRunMetadata,
    SegmentDelta,
)

router = APIRouter(prefix="/runs", tags=["runs"])


_DEFAULT_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 1000
_DEFAULT_HOUR = 12  # Noon UTC — matches the weekly-cron cadence.


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
WHERE id = %(run_id)s
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
ORDER BY scoring_run_timestamp DESC
"""


async def _fetch_run_metadata(
    connection: psycopg.AsyncConnection, run_id: UUID
) -> ScoringRunMetadata:
    """Load one ``scoring_runs`` row into the typed metadata bundle.

    Caller has already established the row exists via
    :func:`api.repos.segment_scores.runs_exist`, so a missing row here
    is a programmer error worth surfacing loudly.
    """
    async with connection.cursor() as cur:
        await cur.execute(_SELECT_RUN_METADATA_SQL, {"run_id": run_id})
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(f"scoring_runs row {run_id} disappeared between runs_exist and fetch")
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


def _resolve_target_hour(t: datetime | None) -> int:
    """Pick the hour-of-day for the JOIN. Defaults to noon UTC."""
    if t is None:
        return _DEFAULT_HOUR
    t_utc = t if t.tzinfo is not None else t.replace(tzinfo=UTC)
    return t_utc.astimezone(UTC).hour


@router.get("", response_model=RunListResponse)
async def list_runs() -> RunListResponse:
    """Return every scoring run with full provenance, newest first.

    Backs the RunPicker (Task 3.3) — the delta endpoint requires the
    caller to already know two run UUIDs, so a separate list endpoint
    is the discovery path. Newest-first matches what the picker shows
    on open: the most recent run pre-selected makes the common-case
    "compare last run to the one before" workflow a single click.

    No pagination yet: scoring runs are weekly, so the list grows at
    ~52 rows/year. If a long-running deploy ever pushes past a useful
    page size, this endpoint can grow ``page`` / ``page_size`` query
    params alongside :class:`RunListResponse` without a breaking
    change.
    """
    async with conn() as connection, connection.cursor() as cur:
        await cur.execute(_LIST_RUNS_SQL)
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


@router.get("/{run_a}/delta/{run_b}", response_model=DeltaResponse)
async def get_runs_delta(
    run_a: UUID,
    run_b: UUID,
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
    """Return a paginated list of per-segment deltas between two runs."""
    if run_a == run_b:
        raise HTTPException(
            status_code=422,
            detail="run_a and run_b must be different scoring runs",
        )

    target_hour = _resolve_target_hour(t)
    offset = (page - 1) * page_size

    async with conn() as connection:
        a_exists, b_exists = await runs_exist(connection, run_a, run_b)
        if not a_exists:
            raise HTTPException(status_code=404, detail=f"scoring run {run_a} not found")
        if not b_exists:
            raise HTTPException(status_code=404, detail=f"scoring run {run_b} not found")

        run_a_meta = await _fetch_run_metadata(connection, run_a)
        run_b_meta = await _fetch_run_metadata(connection, run_b)
        total = await count_pair_at_hour(connection, run_a, run_b, target_hour)
        deltas = [
            _pair_to_delta(pair)
            async for pair in fetch_pair_at_hour(
                connection,
                run_a,
                run_b,
                target_hour,
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
