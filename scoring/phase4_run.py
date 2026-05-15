"""Phase 4 scoring run — composite risk with propagator integration.

This module is the Phase 4 evolution of ``scoring.run``. The Phase 2/3
``ScoringRun`` streams one segment at a time and writes rows
incrementally; Phase 4's propagator needs the **entire** city's
per-hour input vector before it can compute uplift, so the flow here
is a fan-out / collect / fan-in:

1. **Build the graph topology.** Each road segment is one node;
   adjacency is the set of segments whose endpoints share a junction
   (computed once via SQL against ``road_segments``).

2. **Fan out the scorers.** For every (segment, hour) pair, compute
   every configured sub-score; collect into an in-memory
   ``{segment_id: {hour: {scorer_name: SubScoreResult}}}`` map.

3. **Compute per-hour local aggregates.** For each hour, the input
   vector handed to the propagator is the per-segment local aggregate
   (weighted sum of sub-scores). Glare is the only time-varying
   sub-score; junction-complexity + historical-correlation are
   topology-driven and time-invariant.

4. **Run the propagator 24 times.** Each hour gets a fresh
   ``GraphData`` with the same topology but a different input vector.
   The runs are parallelized via the C++ bindings' ``ThreadPoolExecutor``
   (see ``scoring.propagator.runner``).

5. **Assemble composite + write rows.** For every (segment, hour),
   ``composite_risk = local_aggregate + propagation_uplift``. The two
   contributions are persisted separately so the API can ship the
   explainable decomposition (per spec.md §"Explainability").

The module deliberately reuses the Phase 2/3 row template from
``scoring.run`` for the non-Phase-4 columns. Only ``composite_risk``,
``propagation_uplift``, and ``propagation_algorithm_version`` differ
in source.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, cast
from uuid import UUID

import psycopg
import structlog

from scoring.composite import (
    DEFAULT_COMPOSITE_WEIGHTS,
    CompositeBreakdown,
    assemble,
    local_aggregate,
)
from scoring.interface import ScoringSegment, SubScorer, SubScoreResult
from scoring.propagator.runner import (
    DEFAULT_WORKERS,
    PHASE_4_DEFAULT_STRATEGY,
    PropagationCallInputs,
    run_24_hourly,
)
from scoring.run import (
    _INSERT_SCORING_RUN_SQL,
    _INSERT_SEGMENT_SCORE_SQL,
    _SUB_SCORE_REGISTRY,
    STUB_SUB_SCORE_VALUE,
    ScoringRunConfig,
    ScoringRunSummary,
    _to_psycopg_dsn,
    load_scoring_segments,
)

log = structlog.get_logger(__name__)


_BATCH_FLUSH_SIZE: Final[int] = 1000

# SQL to derive segment topology + adjacency from road_segments alone.
# Two segments are adjacent if their geometries share a node (start
# or end point) within a small tolerance — postgis ST_DWithin on
# endpoint geographies. The ``adjacency_pairs`` CTE gives us the edges;
# ``junction_legs`` counts how many segments meet at each endpoint.
_LOAD_ADJACENCY_SQL = """
WITH endpoints AS (
    SELECT
        id AS segment_id,
        ST_StartPoint(geometry) AS start_pt,
        ST_EndPoint(geometry)   AS end_pt
    FROM road_segments
),
canonical_endpoints AS (
    -- Canonicalize each endpoint by snapping to a 6-decimal-degree grid
    -- (~11 cm) so segments whose endpoints differ by floating-point
    -- noise still cluster together. WKT is the comparison key — fast
    -- and bytewise-stable.
    SELECT
        segment_id,
        ST_AsText(ST_SnapToGrid(start_pt, 0.000001)) AS start_key,
        ST_AsText(ST_SnapToGrid(end_pt,   0.000001)) AS end_key
    FROM endpoints
),
edge_pairs AS (
    -- Two segments are adjacent if they share any canonical endpoint.
    -- The DISTINCT collapses both directions of a shared-endpoint match.
    SELECT DISTINCT a.segment_id AS src_id, b.segment_id AS dst_id
    FROM canonical_endpoints a
    JOIN canonical_endpoints b
      ON a.segment_id <> b.segment_id
     AND (a.start_key IN (b.start_key, b.end_key)
          OR a.end_key IN (b.start_key, b.end_key))
)
SELECT src_id, dst_id FROM edge_pairs
"""


# A few helper types for the per-segment per-hour matrices ---------------------
@dataclass(frozen=True, slots=True)
class _SegmentIndex:
    """Stable bidirectional mapping between segment UUIDs and 0-based indices.

    The propagator's graph contract uses 0-based contiguous indices
    for adjacency targets; the UUID-keyed scorer results need to be
    rebound to integer indices when building the graph payload and
    rebound back when reading uplifts.
    """

    id_to_index: dict[UUID, int]
    index_to_id: list[UUID]

    @classmethod
    def from_segments(cls, segments: Sequence[ScoringSegment]) -> _SegmentIndex:
        index_to_id = [s.segment_id for s in segments]
        id_to_index = {sid: i for i, sid in enumerate(index_to_id)}
        return cls(id_to_index=id_to_index, index_to_id=index_to_id)

    def __len__(self) -> int:
        return len(self.index_to_id)


@dataclass(frozen=True, slots=True)
class Phase4ScoringRunSummary(ScoringRunSummary):
    """Phase 4 summary extends the base summary with propagator details."""

    propagation_algorithm: str = ""
    propagation_total_seconds: float = 0.0
    propagation_per_hour_seconds: tuple[float, ...] = ()
    # Use a frozen tuple-of-pairs default to avoid the
    # "mutable default" trap; the surfaced property below restores the
    # Mapping shape callers expect.
    composite_weights_pairs: tuple[tuple[str, float], ...] = tuple(
        DEFAULT_COMPOSITE_WEIGHTS.items()
    )

    @property
    def composite_weights(self) -> Mapping[str, float]:
        return dict(self.composite_weights_pairs)


def _load_adjacency(conn: psycopg.Connection[Any], index: _SegmentIndex) -> list[list[int]]:
    """Build per-segment adjacency list from shared endpoints.

    Returns a list of lists where ``adjacency[i]`` is the set of
    indices of segments adjacent to segment ``i``. Edge weights are
    set to 1.0 here; future strategy implementations may compute
    weights from segment length, road class, etc.
    """
    n = len(index)
    adjacency: list[list[int]] = [[] for _ in range(n)]
    with conn.cursor() as cur:
        cur.execute(_LOAD_ADJACENCY_SQL)
        for src_id, dst_id in cur.fetchall():
            src_uuid = UUID(str(src_id)) if not isinstance(src_id, UUID) else src_id
            dst_uuid = UUID(str(dst_id)) if not isinstance(dst_id, UUID) else dst_id
            src_idx = index.id_to_index.get(src_uuid)
            dst_idx = index.id_to_index.get(dst_uuid)
            if src_idx is None or dst_idx is None:
                continue
            adjacency[src_idx].append(dst_idx)
    return adjacency


def _build_graph_dict(
    index: _SegmentIndex,
    adjacency: Sequence[Sequence[int]],
    inputs: Sequence[float],
    *,
    default_edge_weight: float = 1.0,
) -> dict[str, object]:
    """Build the per-hour ``graph`` dict matching the bindings contract.

    The bindings expect a dict with three keys:
      - ``node_ids``: list[int]   — external NodeId per node
      - ``adjacency``: list[list[(target, weight)]] — outgoing edges
      - ``inputs``: list[float]   — per-node input value
    """
    node_ids = list(range(len(index)))  # use integer indices as NodeIds
    adj: list[list[tuple[int, float]]] = []
    for neighbors in adjacency:
        adj.append([(t, default_edge_weight) for t in neighbors])
    return {
        "node_ids": node_ids,
        "adjacency": adj,
        "inputs": list(inputs),
    }


def _score_all(
    scorers: Sequence[SubScorer],
    segments: Sequence[ScoringSegment],
    ats: Sequence[datetime],
) -> dict[UUID, dict[int, dict[str, SubScoreResult]]]:
    """Run every scorer for every (segment, hour) up-front.

    Returns ``{segment_id: {hour_index: {scorer_name: SubScoreResult}}}``.
    Memory cost: ~ 32 bytes * n_segments * 24 * n_scorers ≈ a few MB
    for a Cambridge-scale run. Worth it: the propagator needs the full
    per-hour input vector before it can run.
    """
    out: dict[UUID, dict[int, dict[str, SubScoreResult]]] = {}
    for segment in segments:
        per_hour: dict[int, dict[str, SubScoreResult]] = {h: {} for h in range(len(ats))}
        for scorer in scorers:
            batch_fn = getattr(scorer, "score_for_samples", None)
            if callable(batch_fn):
                results = list(batch_fn(segment, ats=list(ats)))
            else:
                results = [scorer.score(segment, at=t) for t in ats]
            for h, result in enumerate(results):
                per_hour[h][scorer.name] = result
        out[segment.segment_id] = per_hour
    return out


def _composite_local_inputs_per_hour(
    sub_scores_by_segment: Mapping[UUID, Mapping[int, Mapping[str, SubScoreResult]]],
    index: _SegmentIndex,
    n_hours: int,
    weights: Mapping[str, float],
) -> list[list[float]]:
    """For each hour, produce the per-segment local-aggregate input vector.

    Returns a list of length ``n_hours``; each entry is a list of
    length ``len(index)`` aligned with the index's id ordering. The
    weight dict drives which sub-score names contribute; missing
    sub-scores fall back to ``STUB_SUB_SCORE_VALUE`` (0.0) so the
    aggregate is well-defined even when a scorer is omitted.
    """
    per_hour: list[list[float]] = []
    for h in range(n_hours):
        inputs: list[float] = []
        for seg_idx in range(len(index)):
            seg_id = index.index_to_id[seg_idx]
            scores = sub_scores_by_segment.get(seg_id, {}).get(h, {})
            sub_score_values = {
                name: (scores[name].value if name in scores else STUB_SUB_SCORE_VALUE)
                for name in weights
            }
            inputs.append(local_aggregate(sub_score_values, weights))
        per_hour.append(inputs)
    return per_hour


def _flush(conn: psycopg.Connection[Any], batch: list[dict[str, object]]) -> int:
    if not batch:
        return 0
    with conn.cursor() as cur:
        cur.executemany(_INSERT_SEGMENT_SCORE_SQL, batch)
    conn.commit()
    return len(batch)


def execute_phase4_scoring_run(
    *,
    config: ScoringRunConfig,
    scorers: Sequence[SubScorer],
    database_url: str,
    composite_weights: Mapping[str, float] = DEFAULT_COMPOSITE_WEIGHTS,
    propagation_strategy: str = PHASE_4_DEFAULT_STRATEGY,
    propagation_params: Mapping[str, object] | None = None,
    max_workers: int = DEFAULT_WORKERS,
) -> Phase4ScoringRunSummary:
    """Execute one Phase 4 scoring run, end-to-end.

    Differences from :class:`scoring.run.ScoringRun`:
      - Pre-loads adjacency from PostGIS.
      - Pre-computes all sub-scores for all (segment, hour) before
        the propagator runs (the propagator needs the full per-hour
        input vector at once).
      - Runs the propagator 24 times (one per hour) under the
        ThreadPoolExecutor inside ``scoring.propagator.runner``.
      - Persists ``composite_risk`` + ``propagation_uplift`` as the
        sum-of-local-aggregate + uplift decomposition.
    """
    run_id = uuid.uuid4()
    from datetime import UTC as _UTC

    run_timestamp = datetime.now(_UTC)
    t0 = time.perf_counter()

    dsn = _to_psycopg_dsn(database_url)
    rows_written = 0
    segments_processed = 0
    propagation_total = 0.0
    per_hour_wall: tuple[float, ...] = ()

    if propagation_params is None:
        # Defaults match ADR 0006's Decision for `pagerank-diffusion`
        # (the chosen production strategy):
        #   - decay_weight = 0.85 (standard PageRank damping)
        #   - normalize    = True (per-graph max-rescale so uplift composes with
        #                    local aggregate on a comparable scale)
        #   - k_hop_radius is ignored by `pagerank-diffusion` but kept on the
        #     shared Params struct.
        propagation_params = {"k_hop_radius": 2, "decay_weight": 0.85, "normalize": True}

    with psycopg.connect(dsn) as conn:
        # Record the scoring_runs row up-front so any later error still
        # leaves a traceable provenance entry. (The append-only invariant
        # means we never delete it on failure; failed runs are
        # archaeologically visible.)
        with conn.cursor() as cur:
            cur.execute(
                _INSERT_SCORING_RUN_SQL,
                {
                    "id": run_id,
                    "scoring_run_timestamp": run_timestamp,
                    "perception_model_version": config.perception_model_version,
                    "osm_snapshot_date": config.osm_snapshot_date,
                    "imagery_capture_window": config.imagery_capture_window_daterange,
                    "propagation_algorithm_version": config.propagation_algorithm_version,
                    "notes": config.notes,
                },
            )
        conn.commit()

        segments = list(load_scoring_segments(conn))
        segments_processed = len(segments)
        index = _SegmentIndex.from_segments(segments)

        log.info(
            "phase4_run.start",
            run_id=str(run_id),
            scorers=[s.name for s in scorers],
            segments=len(segments),
            temporal_samples=len(config.temporal_samples),
            propagation_strategy=propagation_strategy,
        )

        t_adj = time.perf_counter()
        adjacency = _load_adjacency(conn, index)
        adjacency_secs = time.perf_counter() - t_adj
        edge_count = sum(len(n) for n in adjacency)
        log.info(
            "phase4_run.adjacency.loaded",
            nodes=len(index),
            edges=edge_count,
            seconds=round(adjacency_secs, 3),
        )

        t_score = time.perf_counter()
        sub_scores = _score_all(scorers, segments, config.temporal_samples)
        scorer_secs = time.perf_counter() - t_score
        log.info(
            "phase4_run.scorers.done",
            seconds=round(scorer_secs, 3),
            per_scorer_per_segment_ms=round(
                (scorer_secs * 1000) / max(1, len(scorers) * len(segments)),
                3,
            ),
        )

        # Per-hour local-aggregate input vectors for the propagator.
        n_hours = len(config.temporal_samples)
        per_hour_inputs = _composite_local_inputs_per_hour(
            sub_scores, index, n_hours, composite_weights
        )

        # Build 24 propagation calls — same topology, different input
        # vectors per hour.
        calls: list[PropagationCallInputs] = []
        for h in range(n_hours):
            calls.append(
                PropagationCallInputs(
                    hour_index=h,
                    graph=_build_graph_dict(index, adjacency, per_hour_inputs[h]),
                    params=dict(propagation_params),
                )
            )

        t_prop = time.perf_counter()
        propagation_result = run_24_hourly(
            calls,
            strategy_id=propagation_strategy,
            max_workers=max_workers,
        )
        propagation_total = time.perf_counter() - t_prop
        per_hour_wall = tuple(
            propagation_result.per_hour_wall_seconds.get(h, 0.0) for h in range(n_hours)
        )
        log.info(
            "phase4_run.propagator.done",
            total_seconds=round(propagation_total, 3),
            per_hour_seconds=[round(s, 4) for s in per_hour_wall],
        )

        # Build rows for every (segment, hour). One row per pair.
        t_write = time.perf_counter()
        batch: list[dict[str, object]] = []
        for h, sample in enumerate(config.temporal_samples):
            uplift_for_hour = propagation_result.per_hour_uplift.get(h, {})
            for seg_idx, seg_id in enumerate(index.index_to_id):
                # Each segment_id keys into sub_scores; missing entries
                # mean the scorer didn't produce a result, which yields
                # a stub fallback. Both shouldn't happen in production
                # but the row template stays robust.
                per_hour = sub_scores.get(seg_id, {})
                hour_scores = per_hour.get(h, {})

                # Sub-score values for the composite calculation.
                sub_score_values: dict[str, float] = {}
                for name in composite_weights:
                    if name in hour_scores:
                        sub_score_values[name] = hour_scores[name].value
                    else:
                        sub_score_values[name] = STUB_SUB_SCORE_VALUE
                # Propagator returns uplift keyed by external NodeId
                # (== seg_idx in our build); coerce both to int for
                # safe lookup.
                uplift_value = float(uplift_for_hour.get(seg_idx, 0.0))
                breakdown: CompositeBreakdown = assemble(
                    sub_score_values, uplift_value, composite_weights
                )

                row: dict[str, object] = {
                    "segment_id": seg_id,
                    "scoring_run_id": run_id,
                    "scoring_run_timestamp": sample,
                    "perception_model_version": config.perception_model_version,
                    "osm_snapshot_date": config.osm_snapshot_date,
                    "imagery_capture_window": config.imagery_capture_window_daterange,
                    "propagation_algorithm_version": config.propagation_algorithm_version,
                    "composite_risk": breakdown.composite_risk,
                    "propagation_uplift": breakdown.propagation_uplift,
                }

                # Per-sub-score columns + stub flags driven by the
                # registry (same convention as scoring/run.py:_build_row_from_results).
                real_confidences: list[float] = []
                for name, cols in _SUB_SCORE_REGISTRY.items():
                    result = hour_scores.get(name)
                    if result is None:
                        row[cols.sub_score_col] = STUB_SUB_SCORE_VALUE
                        row[cols.is_stub_col] = True
                        continue
                    row[cols.sub_score_col] = result.value
                    row[cols.is_stub_col] = result.is_stub
                    if not result.is_stub:
                        real_confidences.append(result.confidence)
                row["confidence"] = min(real_confidences) if real_confidences else 0.0

                batch.append(row)
                if len(batch) >= _BATCH_FLUSH_SIZE:
                    rows_written += _flush(conn, batch)
                    batch = []
        rows_written += _flush(conn, batch)
        write_secs = time.perf_counter() - t_write
        log.info(
            "phase4_run.persistence.done",
            rows_written=rows_written,
            seconds=round(write_secs, 3),
        )

    elapsed = time.perf_counter() - t0
    log.info(
        "phase4_run.done",
        run_id=str(run_id),
        rows_written=rows_written,
        segments_processed=segments_processed,
        seconds=round(elapsed, 3),
    )
    return Phase4ScoringRunSummary(
        run_id=run_id,
        scoring_run_timestamp=run_timestamp,
        rows_written=rows_written,
        segments_processed=segments_processed,
        temporal_samples_count=len(config.temporal_samples),
        seconds_elapsed=elapsed,
        propagation_algorithm=propagation_strategy,
        propagation_total_seconds=propagation_total,
        propagation_per_hour_seconds=per_hour_wall,
        composite_weights_pairs=tuple(composite_weights.items()),
    )


__all__ = [
    "Phase4ScoringRunSummary",
    "execute_phase4_scoring_run",
]


# Re-export for type-checker friendliness; cast quiets unused-import
# warnings if a downstream linter complains.
_ = cast(Any, _INSERT_SCORING_RUN_SQL)
