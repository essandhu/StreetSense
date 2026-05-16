"""Repository for delta-pair reads from ``segment_scores`` (Phase 5 Task 2.3).

Phase 5's delta endpoint compares two scoring runs at a specific
**hour-of-day**. Each scoring_run already produces 24 rows per segment
(one per hourly temporal sample, persisted with
``scoring_run_timestamp`` carrying both date and hour). The natural
JOIN is then ``segment_id`` plus ``extract(hour from
scoring_run_timestamp)`` — noon-against-noon, regardless of which
actual date each weekly run targets.

Plan-vs-actual note: ``conductor/tracks/phase-5-delta-deployment/
plan.md`` Task 2.3 sketched the SQL as ``JOIN segment_scores USING
(segment_id, t)``. The persisted schema has no ``t`` column — Phase 4's
time-varying behavior is achieved via 24 rows per segment per run,
keyed by ``scoring_run_timestamp``. The hour-of-day reduction here is
the literal-schema realization of that sketch. See the track ``index.md``
under "Discovered during implementation".

The route layer (Task 2.4) reconstructs the full
:class:`api.schemas.ConfidenceIndicator` from imagery rows (same pattern
as ``api/routes/segments.py``). This repo returns only the raw scalar
``confidence`` from ``segment_scores``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------


class SegmentScoreRow(BaseModel):
    """One ``segment_scores`` row, raw from the DB.

    Frozen so a row passed between layers can't be mutated by accident.
    Sub-score values and confidence are ``float | None`` because Phase 1
    stub rows wrote NULL for sub-scores that didn't have a real scorer
    yet — current Phase 4+ runs populate all four. The route layer
    coerces nulls (or skips the row) when assembling a
    :class:`api.delta.SegmentScoreSnapshot`.
    """

    model_config = ConfigDict(frozen=True)

    segment_id: UUID
    composite_risk: float
    propagation_uplift: float
    sub_score_lane_marking: float | None = None
    sub_score_glare: float | None = None
    sub_score_junction_complexity: float | None = None
    sub_score_historical: float | None = None
    confidence: float | None = None


class ScorePairRow(BaseModel):
    """The two ``segment_scores`` rows joined at one hour-of-day for one
    segment. ``a`` is from the first run UUID supplied by the caller,
    ``b`` is from the second."""

    model_config = ConfigDict(frozen=True)

    segment_id: UUID
    a: SegmentScoreRow = Field(..., description="Score row from run_a.")
    b: SegmentScoreRow = Field(..., description="Score row from run_b.")


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
#
# Index posture (verified via EXPLAIN in the integration test):
#
#   * ``segment_scores_run_id_idx`` (BTREE on ``scoring_run_id``) narrows
#     each side of the join to one run's rows.
#   * ``segment_scores_segment_id_idx`` (BTREE on ``segment_id``)
#     supports the equi-join condition.
#   * The ``extract(hour from scoring_run_timestamp)`` predicate is not
#     directly indexable without a functional index; if profiling shows
#     this is the gate, a follow-up migration adds
#     ``CREATE INDEX segment_scores_hour_idx ON segment_scores ((extract(hour
#     from scoring_run_timestamp))));`` — anchored in the track index as a
#     Phase 5 follow-up to revisit after the Task 5.3 measurements.

FETCH_PAIR_AT_HOUR_SQL = """
SELECT
    a.segment_id                       AS segment_id,
    a.composite_risk                   AS a_composite_risk,
    a.propagation_uplift               AS a_propagation_uplift,
    a.sub_score_lane_marking           AS a_sub_score_lane_marking,
    a.sub_score_glare                  AS a_sub_score_glare,
    a.sub_score_junction_complexity    AS a_sub_score_junction_complexity,
    a.sub_score_historical             AS a_sub_score_historical,
    a.confidence                       AS a_confidence,
    b.composite_risk                   AS b_composite_risk,
    b.propagation_uplift               AS b_propagation_uplift,
    b.sub_score_lane_marking           AS b_sub_score_lane_marking,
    b.sub_score_glare                  AS b_sub_score_glare,
    b.sub_score_junction_complexity    AS b_sub_score_junction_complexity,
    b.sub_score_historical             AS b_sub_score_historical,
    b.confidence                       AS b_confidence
FROM segment_scores a
INNER JOIN segment_scores b
    ON a.segment_id = b.segment_id
   AND extract(hour from a.scoring_run_timestamp)
     = extract(hour from b.scoring_run_timestamp)
WHERE a.scoring_run_id = %(run_a_id)s
  AND b.scoring_run_id = %(run_b_id)s
  AND extract(hour from a.scoring_run_timestamp) = %(target_hour)s
ORDER BY a.segment_id
LIMIT %(limit)s OFFSET %(offset)s
"""

PAIR_COUNT_AT_HOUR_SQL = """
SELECT count(*)
FROM segment_scores a
INNER JOIN segment_scores b
    ON a.segment_id = b.segment_id
   AND extract(hour from a.scoring_run_timestamp)
     = extract(hour from b.scoring_run_timestamp)
WHERE a.scoring_run_id = %(run_a_id)s
  AND b.scoring_run_id = %(run_b_id)s
  AND extract(hour from a.scoring_run_timestamp) = %(target_hour)s
"""

RUNS_EXIST_SQL = """
SELECT EXISTS (SELECT 1 FROM scoring_runs WHERE id = %(run_id)s)
"""


# ---------------------------------------------------------------------------
# Async repository functions
# ---------------------------------------------------------------------------


async def runs_exist(
    conn: psycopg.AsyncConnection, run_a_id: UUID, run_b_id: UUID
) -> tuple[bool, bool]:
    """Check whether both run IDs reference existing ``scoring_runs`` rows.

    Returns ``(a_exists, b_exists)``. The route layer maps either ``False``
    to a 404 with the missing-run ID surfaced.
    """
    async with conn.cursor() as cur:
        await cur.execute(RUNS_EXIST_SQL, {"run_id": run_a_id})
        a_row = await cur.fetchone()
        await cur.execute(RUNS_EXIST_SQL, {"run_id": run_b_id})
        b_row = await cur.fetchone()
    a_exists = bool(a_row[0]) if a_row else False
    b_exists = bool(b_row[0]) if b_row else False
    return a_exists, b_exists


async def count_pair_at_hour(
    conn: psycopg.AsyncConnection,
    run_a_id: UUID,
    run_b_id: UUID,
    target_hour: int,
) -> int:
    """Count the segments scored in both runs at ``target_hour``.

    ``target_hour`` is an integer in ``[0, 23]`` — typically derived from
    the route's ``t`` query parameter via ``t.astimezone(UTC).hour``.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            PAIR_COUNT_AT_HOUR_SQL,
            {
                "run_a_id": run_a_id,
                "run_b_id": run_b_id,
                "target_hour": target_hour,
            },
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def fetch_pair_at_hour(
    conn: psycopg.AsyncConnection,
    run_a_id: UUID,
    run_b_id: UUID,
    target_hour: int,
    *,
    limit: int,
    offset: int = 0,
) -> AsyncIterator[ScorePairRow]:
    """Stream the paired score rows for the requested hour-of-day.

    Yields one :class:`ScorePairRow` per segment present in both runs at
    ``target_hour``, ordered by ``segment_id`` for stable pagination.

    The query is a single JOIN — no N+1 lookups, no per-row round-trips.
    The route layer aggregates the iterator into the paginated
    :class:`api.schemas.DeltaResponse`.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            FETCH_PAIR_AT_HOUR_SQL,
            {
                "run_a_id": run_a_id,
                "run_b_id": run_b_id,
                "target_hour": target_hour,
                "limit": limit,
                "offset": offset,
            },
        )
        async for row in cur:
            (
                segment_id,
                a_composite_risk,
                a_propagation_uplift,
                a_sub_score_lane_marking,
                a_sub_score_glare,
                a_sub_score_junction_complexity,
                a_sub_score_historical,
                a_confidence,
                b_composite_risk,
                b_propagation_uplift,
                b_sub_score_lane_marking,
                b_sub_score_glare,
                b_sub_score_junction_complexity,
                b_sub_score_historical,
                b_confidence,
            ) = row
            a = SegmentScoreRow(
                segment_id=segment_id,
                composite_risk=float(a_composite_risk),
                propagation_uplift=float(a_propagation_uplift),
                sub_score_lane_marking=(
                    None if a_sub_score_lane_marking is None else float(a_sub_score_lane_marking)
                ),
                sub_score_glare=(None if a_sub_score_glare is None else float(a_sub_score_glare)),
                sub_score_junction_complexity=(
                    None
                    if a_sub_score_junction_complexity is None
                    else float(a_sub_score_junction_complexity)
                ),
                sub_score_historical=(
                    None if a_sub_score_historical is None else float(a_sub_score_historical)
                ),
                confidence=None if a_confidence is None else float(a_confidence),
            )
            b = SegmentScoreRow(
                segment_id=segment_id,
                composite_risk=float(b_composite_risk),
                propagation_uplift=float(b_propagation_uplift),
                sub_score_lane_marking=(
                    None if b_sub_score_lane_marking is None else float(b_sub_score_lane_marking)
                ),
                sub_score_glare=(None if b_sub_score_glare is None else float(b_sub_score_glare)),
                sub_score_junction_complexity=(
                    None
                    if b_sub_score_junction_complexity is None
                    else float(b_sub_score_junction_complexity)
                ),
                sub_score_historical=(
                    None if b_sub_score_historical is None else float(b_sub_score_historical)
                ),
                confidence=None if b_confidence is None else float(b_confidence),
            )
            yield ScorePairRow(segment_id=segment_id, a=a, b=b)


__all__ = [
    "FETCH_PAIR_AT_HOUR_SQL",
    "PAIR_COUNT_AT_HOUR_SQL",
    "RUNS_EXIST_SQL",
    "ScorePairRow",
    "SegmentScoreRow",
    "count_pair_at_hour",
    "fetch_pair_at_hour",
    "runs_exist",
]
