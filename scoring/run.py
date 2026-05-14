"""Scoring run orchestration.

A `ScoringRun` materializes one ``segment_scores`` row per
(road segment, temporal sample) pair. Each row carries:

- The composite + four sub-scores. Phase 2 shipped ``glare`` as the
  first real sub-score; Phase 3 adds ``lane_marking`` (perception);
  the remaining two stay stubbed until Phase 4 (junction +
  historical, the propagator's natural pairings).
- All six reproducibility fields populated (the reproducibility
  invariant from CLAUDE.md / spec.md). When a real perception model /
  propagator does not yet exist, documented sentinels are written so
  the NOT NULL constraint stays satisfied without falsely claiming
  that work was done.

Extension point 1 (new risk factors) is exercised here twice now:
``glare`` (Phase 2) and ``lane_marking`` (Phase 3). The sub-score
registry below maps a scorer's ``name`` to its ``segment_scores``
column triplet. Adding a new sub-score in Phase 4 means appending a
row to ``_SUB_SCORE_REGISTRY`` and supplying a ``SubScorer`` — no
other changes to this module.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Final
from uuid import UUID

import psycopg
import structlog

from scoring import PHASE_2_PROPAGATION_SENTINEL
from scoring.interface import ScoringSegment, SubScorer, SubScoreResult

log = structlog.get_logger(__name__)


# --- Reproducibility sentinels ----------------------------------------------
# These are non-empty values that satisfy the column's NOT NULL constraint
# without claiming that real data was used. Phase 2 ships them for the three
# fields no real component populates yet; Phase 3 replaces
# `perception_model_version` and `imagery_capture_window` with real values,
# leaving only the propagation sentinel.
PHASE_2_PERCEPTION_MODEL_VERSION_SENTINEL: Final[str] = "none-phase-2"
# Single-day sentinel: both endpoints are 1970-01-01. The
# imagery_capture_window_daterange property converts `(d, d)` to the
# half-open `[d, d+1)` PostgreSQL daterange literal, byte-identical to
# the Phase 2 sentinel string `[1970-01-01,1970-01-02)`.
PHASE_2_IMAGERY_WINDOW_SENTINEL: Final[tuple[date, date]] = (
    date(1970, 1, 1),
    date(1970, 1, 1),
)


# Stub values for the sub-scores that have no real scorer this phase.
# Deterministic 0.0 keeps the column NOT-NULL-able if a future migration
# tightens it, and matches the visual convention "no signal = no risk".
# Consumers must read the matching ``is_stub_*`` flag, never the value,
# to know whether the number is meaningful.
STUB_SUB_SCORE_VALUE: Final[float] = 0.0


# --- Sub-score column registry ----------------------------------------------
# Phase 3's name-driven persistence replaces Phase 2's hard-coded glare
# block. Each entry maps a scorer's ``name`` to the three columns the row
# write touches: the sub_score value, the is_stub flag, and the canonical
# "this is real now" tag (purely documentation — useful when grepping for
# which phase flipped a stub).
@dataclass(frozen=True, slots=True)
class _SubScoreColumns:
    sub_score_col: str
    is_stub_col: str
    real_since_phase: int  # 0 if still stubbed; 2 = glare, 3 = lane_marking, ...


_SUB_SCORE_REGISTRY: Final[dict[str, _SubScoreColumns]] = {
    "glare": _SubScoreColumns(
        sub_score_col="sub_score_glare",
        is_stub_col="is_stub_glare",
        real_since_phase=2,
    ),
    "lane_marking": _SubScoreColumns(
        sub_score_col="sub_score_lane_marking",
        is_stub_col="is_stub_lane_marking",
        real_since_phase=3,
    ),
    # Phase 4 entries (placeholders, written as stubs until their scorers ship):
    "junction_complexity": _SubScoreColumns(
        sub_score_col="sub_score_junction_complexity",
        is_stub_col="is_stub_junction_complexity",
        real_since_phase=0,
    ),
    "historical": _SubScoreColumns(
        sub_score_col="sub_score_historical",
        is_stub_col="is_stub_historical",
        real_since_phase=0,
    ),
}


@dataclass(frozen=True, slots=True)
class ScoringRunConfig:
    """Inputs to one scoring-run invocation.

    ``imagery_capture_window`` switched from ``str`` (Phase 2 daterange
    literal) to ``tuple[date, date]`` in Phase 3 so callers compute the
    real ``(min, max)`` from ``segment_imagery``. The persistence layer
    converts to the PostgreSQL ``daterange`` string at insertion.
    """

    temporal_samples: tuple[datetime, ...]
    osm_snapshot_date: date
    perception_model_version: str = PHASE_2_PERCEPTION_MODEL_VERSION_SENTINEL
    imagery_capture_window: tuple[date, date] = PHASE_2_IMAGERY_WINDOW_SENTINEL
    propagation_algorithm_version: str = PHASE_2_PROPAGATION_SENTINEL
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.temporal_samples:
            raise ValueError("temporal_samples must be non-empty")
        for t in self.temporal_samples:
            if t.tzinfo is None:
                raise ValueError(f"temporal sample {t} is not timezone-aware (UTC required)")
        if not self.perception_model_version:
            raise ValueError("perception_model_version must be a non-empty string")
        if not self.propagation_algorithm_version:
            raise ValueError("propagation_algorithm_version must be a non-empty string")
        start, end = self.imagery_capture_window
        if start > end:
            raise ValueError(f"imagery_capture_window start ({start}) is after end ({end})")

    @property
    def imagery_capture_window_daterange(self) -> str:
        """Half-open PostgreSQL ``daterange`` literal for persistence."""
        start, end = self.imagery_capture_window
        # `[start, end+1)` so the end date is *inclusive* in human terms
        # while staying within the PostgreSQL daterange's half-open
        # convention.
        from datetime import timedelta

        return f"[{start.isoformat()},{(end + timedelta(days=1)).isoformat()})"


def default_24_hourly_samples(reference_date: date) -> tuple[datetime, ...]:
    """24 hourly UTC samples for the reference day at 00:00..23:00 UTC."""
    base = datetime(reference_date.year, reference_date.month, reference_date.day, tzinfo=UTC)
    return tuple(base.replace(hour=h) for h in range(24))


@dataclass(frozen=True, slots=True)
class ScoringRunSummary:
    """What the CLI prints at the end of a run."""

    run_id: UUID
    scoring_run_timestamp: datetime
    rows_written: int
    segments_processed: int
    temporal_samples_count: int
    seconds_elapsed: float


# --- Persistence ------------------------------------------------------------
_INSERT_SCORING_RUN_SQL = """
INSERT INTO scoring_runs (
    id,
    scoring_run_timestamp,
    perception_model_version,
    osm_snapshot_date,
    imagery_capture_window,
    propagation_algorithm_version,
    notes
)
VALUES (
    %(id)s,
    %(scoring_run_timestamp)s,
    %(perception_model_version)s,
    %(osm_snapshot_date)s,
    %(imagery_capture_window)s::daterange,
    %(propagation_algorithm_version)s,
    %(notes)s
)
"""

_INSERT_SEGMENT_SCORE_SQL = """
INSERT INTO segment_scores (
    segment_id,
    composite_risk,
    sub_score_lane_marking,
    sub_score_glare,
    sub_score_junction_complexity,
    sub_score_historical,
    confidence,
    is_stub_lane_marking,
    is_stub_glare,
    is_stub_junction_complexity,
    is_stub_historical,
    scoring_run_id,
    scoring_run_timestamp,
    perception_model_version,
    osm_snapshot_date,
    imagery_capture_window,
    propagation_algorithm_version
)
VALUES (
    %(segment_id)s,
    %(composite_risk)s,
    %(sub_score_lane_marking)s,
    %(sub_score_glare)s,
    %(sub_score_junction_complexity)s,
    %(sub_score_historical)s,
    %(confidence)s,
    %(is_stub_lane_marking)s,
    %(is_stub_glare)s,
    %(is_stub_junction_complexity)s,
    %(is_stub_historical)s,
    %(scoring_run_id)s,
    %(scoring_run_timestamp)s,
    %(perception_model_version)s,
    %(osm_snapshot_date)s,
    %(imagery_capture_window)s::daterange,
    %(propagation_algorithm_version)s
)
"""

_SELECT_SEGMENTS_SQL = """
SELECT
    id,
    ST_Y(ST_StartPoint(geometry)) AS start_lat,
    ST_X(ST_StartPoint(geometry)) AS start_lon,
    ST_Y(ST_EndPoint(geometry))   AS end_lat,
    ST_X(ST_EndPoint(geometry))   AS end_lon,
    ST_Y(ST_Centroid(geometry))   AS centroid_lat,
    ST_X(ST_Centroid(geometry))   AS centroid_lon
FROM road_segments
"""


def _to_psycopg_dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _bearing_deg(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> float:
    """Initial bearing from start to end on a sphere, degrees clockwise from north."""
    phi1 = math.radians(start_lat)
    phi2 = math.radians(end_lat)
    delta_lambda = math.radians(end_lon - start_lon)
    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    theta = math.atan2(y, x)
    return (math.degrees(theta) + 360.0) % 360.0


def load_scoring_segments(
    conn: psycopg.Connection[tuple[object, ...]],
) -> Iterator[ScoringSegment]:
    """Stream every ``road_segments`` row as a ``ScoringSegment``."""
    with conn.cursor() as cur:
        cur.execute(_SELECT_SEGMENTS_SQL)
        for row in cur:
            seg_id = UUID(str(row[0])) if not isinstance(row[0], UUID) else row[0]
            s_lat = float(row[1])  # type: ignore[arg-type]
            s_lon = float(row[2])  # type: ignore[arg-type]
            e_lat = float(row[3])  # type: ignore[arg-type]
            e_lon = float(row[4])  # type: ignore[arg-type]
            c_lat = float(row[5])  # type: ignore[arg-type]
            c_lon = float(row[6])  # type: ignore[arg-type]
            heading = _bearing_deg(s_lat, s_lon, e_lat, e_lon)
            yield ScoringSegment(
                segment_id=seg_id,
                heading_deg=heading,
                lat=c_lat,
                lon=c_lon,
            )


class ScoringRun:
    """One end-to-end scoring run.

    Stateless except for the inputs handed in via the constructor.
    ``execute()`` is the entry point; everything happens inside.
    """

    def __init__(
        self,
        *,
        config: ScoringRunConfig,
        scorers: Sequence[SubScorer],
        database_url: str,
    ) -> None:
        self._config = config
        self._scorers = {s.name: s for s in scorers}
        self._database_url = database_url
        if len(self._scorers) != len(scorers):
            raise ValueError("Scorer names must be unique")

    @property
    def scorer_names(self) -> frozenset[str]:
        return frozenset(self._scorers)

    def execute(self, *, batch_size: int = 1000) -> ScoringRunSummary:
        """Stream segments x temporal samples and persist their scores."""
        run_id = uuid.uuid4()
        run_timestamp = datetime.now(UTC)
        t0 = time.perf_counter()

        dsn = _to_psycopg_dsn(self._database_url)
        rows_written = 0
        segments_processed = 0

        log.info(
            "scoring_run.start",
            run_id=str(run_id),
            scorers=sorted(self._scorers),
            temporal_samples=len(self._config.temporal_samples),
            scoring_run_timestamp=run_timestamp.isoformat(),
        )

        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _INSERT_SCORING_RUN_SQL,
                    {
                        "id": run_id,
                        "scoring_run_timestamp": run_timestamp,
                        "perception_model_version": self._config.perception_model_version,
                        "osm_snapshot_date": self._config.osm_snapshot_date,
                        "imagery_capture_window": (self._config.imagery_capture_window_daterange),
                        "propagation_algorithm_version": (
                            self._config.propagation_algorithm_version
                        ),
                        "notes": self._config.notes,
                    },
                )
            conn.commit()

            batch: list[dict[str, object]] = []
            for segment in load_scoring_segments(conn):
                segments_processed += 1
                # Batched scorer fan-out per segment: ask each scorer for
                # all temporal_samples at once when it offers
                # `score_for_samples`, else fall back to per-call score.
                results_by_scorer = self._score_segment_for_all_samples(segment)
                for i, sample in enumerate(self._config.temporal_samples):
                    batch.append(
                        self._build_row_from_results(
                            segment, sample, run_id, {n: r[i] for n, r in results_by_scorer.items()}
                        )
                    )
                    if len(batch) >= batch_size:
                        rows_written += _flush(conn, batch)
                        batch = []
            rows_written += _flush(conn, batch)

        elapsed = time.perf_counter() - t0
        log.info(
            "scoring_run.done",
            run_id=str(run_id),
            rows_written=rows_written,
            segments_processed=segments_processed,
            temporal_samples=len(self._config.temporal_samples),
            seconds=round(elapsed, 3),
        )
        return ScoringRunSummary(
            run_id=run_id,
            scoring_run_timestamp=run_timestamp,
            rows_written=rows_written,
            segments_processed=segments_processed,
            temporal_samples_count=len(self._config.temporal_samples),
            seconds_elapsed=elapsed,
        )

    def _score_segment_for_all_samples(self, segment: ScoringSegment) -> dict[str, list[Any]]:
        """Return ``{scorer_name: [result_for_t0, ..., result_for_tN]}``.

        Uses each scorer's batched ``score_for_samples`` when present;
        else loops over ``score`` per timestamp.
        """
        out: dict[str, list[Any]] = {}
        ats = list(self._config.temporal_samples)
        for name, scorer in self._scorers.items():
            batch_fn = getattr(scorer, "score_for_samples", None)
            if callable(batch_fn):
                out[name] = list(batch_fn(segment, ats=ats))
            else:
                out[name] = [scorer.score(segment, at=t) for t in ats]
        return out

    def _build_row_from_results(
        self,
        segment: ScoringSegment,
        sample: datetime,
        run_id: UUID,
        results: dict[str, Any],
    ) -> dict[str, object]:
        """Build one ``segment_scores`` row from the per-scorer results.

        Name-driven via ``_SUB_SCORE_REGISTRY`` so adding a new
        sub-score in Phase 4+ is a registry entry + a SubScorer
        instance — no edits here. The composite risk and confidence
        scalar are *transitional*: Phase 3 carries the only-real-signal
        composite (mean of real sub-scores) and the per-sub-score
        confidence; Phase 3.5's API confidence assembly replaces the
        scalar at the consumer layer, and Phase 4's propagator
        replaces the composite at this layer.
        """
        row: dict[str, object] = {
            "segment_id": segment.segment_id,
            "scoring_run_id": run_id,
            "scoring_run_timestamp": sample,
            "perception_model_version": self._config.perception_model_version,
            "osm_snapshot_date": self._config.osm_snapshot_date,
            "imagery_capture_window": self._config.imagery_capture_window_daterange,
            "propagation_algorithm_version": self._config.propagation_algorithm_version,
        }

        # Per-sub-score columns + provisional composite/confidence.
        real_values: list[float] = []
        real_confidences: list[float] = []
        for name, cols in _SUB_SCORE_REGISTRY.items():
            result: SubScoreResult | None = results.get(name)
            if result is None:
                # No scorer for this column in this run → write stub.
                row[cols.sub_score_col] = STUB_SUB_SCORE_VALUE
                row[cols.is_stub_col] = True
                continue
            row[cols.sub_score_col] = result.value
            row[cols.is_stub_col] = result.is_stub
            if not result.is_stub:
                real_values.append(result.value)
                real_confidences.append(result.confidence)

        # Phase 3 composite: mean of real sub-scores (1 = glare-only in
        # Phase 2; 2 = glare + lane_marking in Phase 3). Phase 4's
        # propagator replaces this. If every real scorer fell back to
        # stub for this segment, composite = 0 (mirrors the
        # is_stub == True ⇒ value == 0 convention).
        row["composite_risk"] = (
            sum(real_values) / len(real_values) if real_values else STUB_SUB_SCORE_VALUE
        )
        # Phase 3 scalar confidence: min of per-sub-score confidences
        # (parallels Tech Note 4's min-rule but applied at the
        # sub-score-confidence axis rather than the freshness /
        # coverage / model-uncertainty axis). The API layer assembles
        # the explainable confidence-with-limiter at request time
        # (Phase 3.5); this scalar is the persisted projection.
        row["confidence"] = min(real_confidences) if real_confidences else 0.0
        return row


def _flush(conn: psycopg.Connection[tuple[object, ...]], batch: Iterable[dict[str, object]]) -> int:
    rows = list(batch)
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(_INSERT_SEGMENT_SCORE_SQL, rows)
    conn.commit()
    return len(rows)


__all__ = [
    "PHASE_2_IMAGERY_WINDOW_SENTINEL",
    "PHASE_2_PERCEPTION_MODEL_VERSION_SENTINEL",
    "STUB_SUB_SCORE_VALUE",
    "ScoringRun",
    "ScoringRunConfig",
    "ScoringRunSummary",
    "default_24_hourly_samples",
    "load_scoring_segments",
]
