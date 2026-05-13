"""Scoring run orchestration.

A `ScoringRun` materializes one ``segment_scores`` row per
(road segment, temporal sample) pair. Each row carries:

- The composite + four sub-scores. In Phase 2, only ``glare`` is real;
  the other three are stub-zero values, with the matching ``is_stub_*``
  column set to true.
- All six reproducibility fields populated (the
  reproducibility invariant from CLAUDE.md / spec.md). When a real
  perception model / propagator does not yet exist, documented Phase-2
  sentinels are written so the NOT NULL constraint stays satisfied
  without falsely claiming work was done.

Extension point 1 (new risk factors) is exercised here for the first
time: the run accepts any sequence of `SubScorer` implementations,
indexed by their ``name``. Phase 3 will add a perception scorer to the
list with no changes to this module.
"""

from __future__ import annotations

import math
import time
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final
from uuid import UUID

import psycopg
import structlog

from scoring import PHASE_2_PROPAGATION_SENTINEL
from scoring.interface import ScoringSegment, SubScorer

log = structlog.get_logger(__name__)


# --- Phase 2 sentinels ------------------------------------------------------
# Each of these is a non-empty value that satisfies the column's
# NOT NULL constraint without claiming that real data was used.
PHASE_2_PERCEPTION_MODEL_VERSION_SENTINEL: Final[str] = "none-phase-2"
PHASE_2_IMAGERY_WINDOW_SENTINEL: Final[str] = "[1970-01-01,1970-01-02)"


# Stub values for the three non-glare sub-scores. Deterministic 0.0 keeps
# the column NOT-NULL-able if a future migration tightens it, and matches
# the visual convention that "no signal = no risk". Consumers must read
# the matching ``is_stub_*`` flag, never the value, to know whether the
# number is meaningful.
STUB_SUB_SCORE_VALUE: Final[float] = 0.0


@dataclass(frozen=True, slots=True)
class ScoringRunConfig:
    """Inputs to one scoring-run invocation."""

    temporal_samples: tuple[datetime, ...]
    osm_snapshot_date: date
    perception_model_version: str = PHASE_2_PERCEPTION_MODEL_VERSION_SENTINEL
    imagery_capture_window: str = PHASE_2_IMAGERY_WINDOW_SENTINEL
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
        if not self.imagery_capture_window:
            raise ValueError("imagery_capture_window must be a non-empty string")


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
        """Stream segments × temporal samples and persist their scores."""
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
                        "imagery_capture_window": self._config.imagery_capture_window,
                        "propagation_algorithm_version": self._config.propagation_algorithm_version,
                        "notes": self._config.notes,
                    },
                )
            conn.commit()

            batch: list[dict[str, object]] = []
            for segment in load_scoring_segments(conn):
                segments_processed += 1
                for sample in self._config.temporal_samples:
                    batch.append(self._build_row(segment, sample, run_id))
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

    def _build_row(
        self,
        segment: ScoringSegment,
        sample: datetime,
        run_id: UUID,
    ) -> dict[str, object]:
        results = {name: scorer.score(segment, at=sample) for name, scorer in self._scorers.items()}

        glare = results.get("glare")
        if glare is None:
            glare_value = STUB_SUB_SCORE_VALUE
            is_stub_glare = True
            confidence = 0.0
        else:
            glare_value = glare.value
            is_stub_glare = glare.is_stub
            # Phase 2: confidence is taken from the only real sub-score.
            # Phase 3 will assemble confidence across sub-scores.
            confidence = glare.confidence

        # The other three sub-scores have no real scorers in Phase 2.
        return {
            "segment_id": segment.segment_id,
            "composite_risk": glare_value,  # Phase 2: composite = glare (only real signal)
            "sub_score_lane_marking": STUB_SUB_SCORE_VALUE,
            "sub_score_glare": glare_value,
            "sub_score_junction_complexity": STUB_SUB_SCORE_VALUE,
            "sub_score_historical": STUB_SUB_SCORE_VALUE,
            "confidence": confidence,
            "is_stub_lane_marking": True,
            "is_stub_glare": is_stub_glare,
            "is_stub_junction_complexity": True,
            "is_stub_historical": True,
            "scoring_run_id": run_id,
            "scoring_run_timestamp": sample,
            "perception_model_version": self._config.perception_model_version,
            "osm_snapshot_date": self._config.osm_snapshot_date,
            "imagery_capture_window": self._config.imagery_capture_window,
            "propagation_algorithm_version": self._config.propagation_algorithm_version,
        }


def _flush(
    conn: psycopg.Connection[tuple[object, ...]], batch: Iterable[dict[str, object]]
) -> int:
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
