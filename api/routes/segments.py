"""City-scoped segment endpoints — Phase 4b Task 3.3 router refactor.

Routes mounted under ``/api/cities/{slug}/segments``:

- ``GET /api/cities/{slug}/segments?limit=N`` — list endpoint (NEW in
  Phase 4b). Returns the city's segments with the latest available
  score (or a stub fallback for segments not yet scored). Each row
  carries the full composite decomposition + sub-scores
  (explainability invariant on the list path).

- ``GET /api/cities/{slug}/segments/{segment_id}`` — segment detail
  (moved from ``/segments/{segment_id}``). Same response shape as
  before; an additional ``WHERE rs.city_id = %(city_id)s`` guards
  against cross-city access (cambridge segment requested under
  ``/api/cities/phoenix/...`` returns 404 instead of leaking).

Phase 3 (API 3.0) detail-shape notes still apply:

- ``confidence`` is a ``ConfidenceIndicator`` object (``{value,
  limiter}``).
- ``imagery`` is an array of pre-signed MinIO URLs (5-minute TTL).
- ``lane_marking_quality.metadata`` carries ``model_uncertainty``.

Phase 4 (API 4.0, non-breaking) additions still apply:

- ``propagation_uplift`` / ``local_contribution`` split
  ``composite_risk`` into its components.
- ``propagation_algorithm`` ships as ``{name, version}`` or ``None``
  for pre-Phase-4 (sentinel) rows.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from minio import Minio

from api.confidence import ConfidenceIndicator as DomainConfidence
from api.confidence import assemble, coverage, freshness
from api.db import conn
from api.dependencies import resolve_city_id
from api.schemas import (
    ConfidenceIndicator,
    ImageryReference,
    PropagationAlgorithmInfo,
    SegmentDetail,
    SegmentListResponse,
    SegmentSummary,
    SubScore,
    SubScores,
)
from api.scoring_stub import stub_risk

# Phase 2 sentinel persisted in scoring_runs.propagation_algorithm_version
# before Phase 4 ships a real propagator. SegmentDetail emits
# ``propagation_algorithm = None`` when this is observed so the UI can
# render the legacy Phase 2/3 segment-detail view without a fake algo
# label.
_PHASE_2_PROPAGATION_SENTINEL = "none-phase-2"


def _parse_propagation_version(version: str | None) -> PropagationAlgorithmInfo | None:
    """Split a persisted ``<name>-<semver>`` string into the typed pair.

    Phase 4 writes e.g. ``"pagerank-diffusion-0.1.0"``; the
    ``rsplit("-", 1)`` gives the algorithm name and the semver. Returns
    ``None`` for empty values or the pre-Phase-4 sentinel.
    """
    if not version or version == _PHASE_2_PROPAGATION_SENTINEL:
        return None
    name, sep, semver = version.rpartition("-")
    if not sep or not name:
        # Garbled value: surface the whole string as the name to keep
        # the response well-typed; the UI can flag this if needed.
        return PropagationAlgorithmInfo(name=version, version="")
    return PropagationAlgorithmInfo(name=name, version=semver)


# Phase 4b: prefix carries the city slug. The ``resolve_city_id``
# dependency runs on every handler and converts ``{slug}`` to the
# matching ``cities.id`` UUID for downstream filtering.
router = APIRouter(prefix="/api/cities/{slug}/segments", tags=["segments"])


# --- MinIO client (module-level: cheap to share; thread-safe) -----------
_MINIO_BUCKET_IMAGERY = "streetsense-imagery"
_PRESIGNED_URL_TTL_SECONDS = 5 * 60  # 5 minutes per spec Phase 3.5.5
# Imagery sampling cadence target: matches ingestion default (5 samples / segment).
# Used by the confidence-assembly `coverage` input.
_DEFAULT_IMAGERY_TARGET_SAMPLES = 5


def _minio_client() -> Minio:
    """Construct a MinIO client from env. Cheap; safe to call per request."""
    return Minio(
        os.environ.get("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.environ.get("MINIO_ROOT_USER", "streetsense"),
        secret_key=os.environ.get("MINIO_ROOT_PASSWORD", "streetsense"),
        secure=False,
    )


def _build_subscore(
    value: float | None,
    is_stub: bool,
    confidence: float,
    metadata: dict[str, Any] | None = None,
) -> SubScore:
    return SubScore(
        value=value if value is not None else 0.0,
        confidence=confidence,
        is_stub=is_stub,
        metadata=metadata or {},
    )


def _to_pydantic_confidence(domain: DomainConfidence) -> ConfidenceIndicator:
    return ConfidenceIndicator(value=domain.value, limiter=domain.limiter)


# The detail SELECT adds ``AND rs.city_id = %(city_id)s`` so a wrong-city
# segment fetch falls through to the 404 path instead of leaking the
# row. Same shape as Phase 4; only the WHERE clause grew.
_SELECT_SNAPPED_SCORE_SQL = """
SELECT
    rs.id            AS segment_id,
    rs.osm_way_id    AS osm_way_id,
    rs.attrs         AS attrs,
    ss.composite_risk,
    ss.propagation_uplift,
    ss.sub_score_lane_marking,
    ss.sub_score_glare,
    ss.sub_score_junction_complexity,
    ss.sub_score_historical,
    ss.confidence,
    ss.is_stub_lane_marking,
    ss.is_stub_glare,
    ss.is_stub_junction_complexity,
    ss.is_stub_historical,
    ss.scoring_run_timestamp,
    sr.propagation_algorithm_version
FROM road_segments rs
LEFT JOIN LATERAL (
    SELECT *
    FROM segment_scores s
    WHERE s.segment_id = rs.id
      AND (%(t)s::timestamptz IS NULL OR true)
    ORDER BY
      CASE WHEN %(t)s::timestamptz IS NULL THEN 0 ELSE 1 END,
      CASE
        WHEN %(t)s::timestamptz IS NULL THEN s.inserted_at
        ELSE NULL
      END DESC,
      CASE
        WHEN %(t)s::timestamptz IS NULL THEN NULL
        ELSE abs(extract(epoch FROM (s.scoring_run_timestamp - %(t)s::timestamptz)))
      END ASC
    LIMIT 1
) ss ON true
LEFT JOIN scoring_runs sr ON sr.id = ss.scoring_run_id
WHERE rs.id = %(id)s
  AND rs.city_id = %(city_id)s
"""

_SELECT_IMAGERY_SQL = """
SELECT
    provider,
    provider_image_id,
    capture_date,
    heading_deg,
    camera_params,
    object_key
FROM segment_imagery
WHERE segment_id = %(id)s
ORDER BY sample_index
"""

# Per-city segment list. Picks the most-recent score per segment via
# DISTINCT ON, then returns the segments themselves so segments that
# haven't been scored yet still appear (with a stub fallback). The
# ``LEFT JOIN LATERAL`` lookup keeps the query a single round-trip
# regardless of how many segments the city carries.
_SELECT_SEGMENT_LIST_SQL = """
SELECT
    rs.id            AS segment_id,
    rs.osm_way_id    AS osm_way_id,
    ss.composite_risk,
    ss.propagation_uplift,
    ss.sub_score_lane_marking,
    ss.sub_score_glare,
    ss.sub_score_junction_complexity,
    ss.sub_score_historical,
    ss.confidence,
    ss.is_stub_lane_marking,
    ss.is_stub_glare,
    ss.is_stub_junction_complexity,
    ss.is_stub_historical
FROM road_segments rs
LEFT JOIN LATERAL (
    SELECT *
    FROM segment_scores s
    WHERE s.segment_id = rs.id
    ORDER BY s.inserted_at DESC
    LIMIT 1
) ss ON true
WHERE rs.city_id = %(city_id)s
ORDER BY rs.id
LIMIT %(limit)s
"""


async def _load_imagery_rows(segment_id: UUID) -> list[dict[str, Any]]:
    async with conn() as c, c.cursor() as cur:
        await cur.execute(_SELECT_IMAGERY_SQL, {"id": segment_id})
        rows = await cur.fetchall()
    return [
        {
            "provider": r[0],
            "provider_image_id": r[1],
            "capture_date": r[2],
            "heading_deg": r[3],
            "camera_params": r[4] or {},
            "object_key": r[5],
        }
        for r in rows
    ]


def _build_imagery_references(rows: list[dict[str, Any]]) -> list[ImageryReference]:
    """Compute pre-signed MinIO URLs per row and pack into the API shape."""
    if not rows:
        return []
    from datetime import timedelta

    client = _minio_client()
    refs: list[ImageryReference] = []
    for row in rows:
        url = client.presigned_get_object(
            bucket_name=_MINIO_BUCKET_IMAGERY,
            object_name=row["object_key"],
            expires=timedelta(seconds=_PRESIGNED_URL_TTL_SECONDS),
        )
        refs.append(
            ImageryReference(
                url=url,
                provider=row["provider"],
                capture_date=row["capture_date"],
                heading_deg=float(row["heading_deg"]),
                camera_params=row["camera_params"] or {},
            )
        )
    return refs


def _confidence_inputs(
    imagery_rows: list[dict[str, Any]],
    model_uncertainty: float,
    *,
    now: date,
    target_samples: int = _DEFAULT_IMAGERY_TARGET_SAMPLES,
) -> DomainConfidence:
    """Assemble the confidence indicator from imagery + model-uncertainty inputs."""
    if not imagery_rows:
        # No imagery → coverage drives the indicator to zero, limiter
        # is 'coverage'. Freshness and model are clamped to the
        # neutral end so they don't accidentally win the tie-break.
        return assemble(freshness_value=1.0, coverage_value=0.0, model_uncertainty=1.0)
    capture_dates = [r["capture_date"] for r in imagery_rows]
    capture_max = max(capture_dates)
    fresh = freshness(capture_max, now=now)
    cov = coverage(actual_samples=len(imagery_rows), target_samples=target_samples)
    return assemble(freshness_value=fresh, coverage_value=cov, model_uncertainty=model_uncertainty)


def _summary_from_row(row: tuple[Any, ...]) -> SegmentSummary:
    """Pack one ``_SELECT_SEGMENT_LIST_SQL`` row into the API shape.

    Segments without a ``segment_scores`` row fall through to a stub
    composite (so the list always returns sensible numbers), matching
    the detail endpoint's "no run yet" fallback. Confidence is left
    ``None`` in that branch — the list endpoint omits per-row imagery
    lookups (would be N+1 over the city's segments) so the rich
    confidence-assembly path isn't available here.
    """
    (
        seg_id,
        osm_way_id,
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

    if composite_risk is None:
        # Segment ingested but not yet scored → stub fallback.
        stub = stub_risk(seg_id)
        return SegmentSummary(
            segment_id=seg_id,
            osm_way_id=osm_way_id,
            composite_risk=stub.composite,
            local_contribution=stub.composite,
            propagation_uplift=0.0,
            sub_scores=SubScores(
                lane_marking_quality=_build_subscore(
                    stub.sub_scores.lane_marking_quality, is_stub=True, confidence=0.0
                ),
                glare_exposure=_build_subscore(
                    stub.sub_scores.glare_exposure, is_stub=True, confidence=0.0
                ),
                junction_complexity=_build_subscore(
                    stub.sub_scores.junction_complexity, is_stub=True, confidence=0.0
                ),
                historical_correlation=_build_subscore(
                    stub.sub_scores.historical_correlation, is_stub=True, confidence=0.0
                ),
            ),
            confidence=None,
        )

    composite_value = float(composite_risk)
    uplift_value = float(propagation_uplift) if propagation_uplift is not None else 0.0
    local_value = max(0.0, composite_value - uplift_value)
    conf_value = float(scalar_confidence) if scalar_confidence is not None else 0.0
    return SegmentSummary(
        segment_id=seg_id,
        osm_way_id=osm_way_id,
        composite_risk=composite_value,
        local_contribution=local_value,
        propagation_uplift=uplift_value,
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
        # The list path doesn't run the imagery-driven confidence
        # assembly (would be N+1); we ship the scalar from
        # segment_scores under the conservative "model" limiter so the
        # shape is correct. Detail endpoint remains the canonical
        # source of truth for the rich confidence breakdown.
        confidence=ConfidenceIndicator(value=conf_value, limiter="model"),
    )


@router.get("", response_model=SegmentListResponse)
async def list_segments(
    city_id: UUID = Depends(resolve_city_id),  # noqa: B008 - FastAPI's Depends() default is idiomatic
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description=(
            "Maximum segments to return. Capped at 500 to keep the "
            "response within the tile-style payload budget."
        ),
    ),
) -> SegmentListResponse:
    """List the city's segments with their latest scores.

    The list endpoint is a tile-adjacent read for clients that don't
    want vector-tile geometry yet (or for non-map UIs). Pagination is
    intentionally minimal — a single ``limit`` parameter — because the
    map case is served by ``/tiles/...`` (Task 3.6) and the off-map
    case has no strong pagination need.
    """
    async with conn() as c, c.cursor() as cur:
        await cur.execute(_SELECT_SEGMENT_LIST_SQL, {"city_id": city_id, "limit": limit})
        rows = await cur.fetchall()

    return SegmentListResponse(segments=[_summary_from_row(row) for row in rows])


@router.get("/{segment_id}", response_model=SegmentDetail)
async def get_segment(
    segment_id: UUID,
    city_id: UUID = Depends(resolve_city_id),  # noqa: B008 - FastAPI's Depends() default is idiomatic
    t: datetime | None = Query(  # noqa: B008 - FastAPI's Query() as default is the idiomatic pattern
        default=None,
        description=(
            "Optional ISO-8601 UTC instant. Snaps to the nearest persisted "
            "hourly sample in segment_scores. Omitted ⇒ most recent score row."
        ),
    ),
) -> SegmentDetail:
    """Return the per-segment detail payload (API 3.0).

    A segment fetched under the wrong city slug returns 404, not the
    row — the ``WHERE rs.city_id = %(city_id)s`` clause guards
    cross-city access at the query layer.
    """
    t_param: datetime | None = None
    if t is not None:
        t_param = t if t.tzinfo is not None else t.replace(tzinfo=UTC)

    async with conn() as c, c.cursor() as cur:
        await cur.execute(
            _SELECT_SNAPPED_SCORE_SQL,
            {"id": segment_id, "t": t_param, "city_id": city_id},
        )
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"segment {segment_id} not found")

    (
        seg_id,
        osm_way_id,
        attrs,
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
        scoring_run_timestamp,
        propagation_algorithm_version,
    ) = row

    # Load imagery rows once. Used both for the response `imagery`
    # array and as input to the confidence assembly.
    imagery_rows = await _load_imagery_rows(seg_id)
    imagery_refs = _build_imagery_references(imagery_rows)

    # Model uncertainty: the perception scorer's metadata isn't
    # persisted per segment_scores row (would bloat the table). The
    # scalar confidence stored on the row is the min across
    # per-sub-score confidences; perception's confidence is
    # `1 - model_uncertainty`. When perception is the limiter we can
    # back out uncertainty from the stored confidence. When perception
    # is not the limiter we conservatively use the same stored
    # confidence — slight over-statement of uncertainty when glare is
    # the limiter, acceptable for Phase 3 (Phase 4 carries
    # `model_uncertainty` per row in its own column if profiling shows
    # this is too coarse).
    if scalar_confidence is None:
        model_uncertainty = 1.0
    else:
        model_uncertainty = max(0.0, 1.0 - float(scalar_confidence))

    confidence_domain = _confidence_inputs(
        imagery_rows=imagery_rows,
        model_uncertainty=model_uncertainty,
        now=datetime.now(UTC).date(),
    )

    if composite_risk is None:
        # No `segment_scores` row exists for this segment yet (e.g.,
        # the scoring run hasn't been executed since this segment was
        # ingested). Fall back to the Phase 1 stub so the endpoint
        # stays useful for newly-ingested segments before the next
        # scoring run.
        stub = stub_risk(seg_id)
        return SegmentDetail(
            segment_id=seg_id,
            osm_way_id=osm_way_id,
            composite_risk=stub.composite,
            local_contribution=stub.composite,
            propagation_uplift=0.0,
            propagation_algorithm=None,
            sub_scores=SubScores(
                lane_marking_quality=_build_subscore(
                    stub.sub_scores.lane_marking_quality, is_stub=True, confidence=0.0
                ),
                glare_exposure=_build_subscore(
                    stub.sub_scores.glare_exposure, is_stub=True, confidence=0.0
                ),
                junction_complexity=_build_subscore(
                    stub.sub_scores.junction_complexity, is_stub=True, confidence=0.0
                ),
                historical_correlation=_build_subscore(
                    stub.sub_scores.historical_correlation, is_stub=True, confidence=0.0
                ),
            ),
            confidence=_to_pydantic_confidence(confidence_domain),
            imagery=imagery_refs,
            attrs=attrs or {},
        )

    glare_metadata: dict[str, Any] = {}
    if not is_stub_glare and scoring_run_timestamp is not None:
        glare_metadata = await _compute_glare_metadata(seg_id, scoring_run_timestamp)

    lane_metadata: dict[str, Any] = {}
    if not is_stub_lane:
        lane_metadata = {
            "image_count": len(imagery_rows),
            "model_uncertainty": model_uncertainty,
        }

    uplift_value = float(propagation_uplift) if propagation_uplift is not None else 0.0
    composite_value = float(composite_risk)
    local_value = max(0.0, composite_value - uplift_value)
    return SegmentDetail(
        segment_id=seg_id,
        osm_way_id=osm_way_id,
        composite_risk=composite_value,
        local_contribution=local_value,
        propagation_uplift=uplift_value,
        propagation_algorithm=_parse_propagation_version(propagation_algorithm_version),
        sub_scores=SubScores(
            lane_marking_quality=_build_subscore(
                None if sub_lane is None else float(sub_lane),
                is_stub=bool(is_stub_lane),
                confidence=(float(scalar_confidence) if scalar_confidence is not None else 0.0),
                metadata=lane_metadata,
            ),
            glare_exposure=_build_subscore(
                None if sub_glare is None else float(sub_glare),
                is_stub=bool(is_stub_glare),
                confidence=(float(scalar_confidence) if scalar_confidence is not None else 0.0),
                metadata=glare_metadata,
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
        confidence=_to_pydantic_confidence(confidence_domain),
        imagery=imagery_refs,
        attrs=attrs or {},
    )


async def _compute_glare_metadata(segment_id: UUID, at: datetime) -> dict[str, Any]:
    """Recompute sun_azimuth_deg / sun_elevation_deg for the segment at ``at``.

    Pure-functional and cheap; cheaper than persisting the metadata
    JSON. The scorer is the source of truth for the math.
    """
    from scoring.environmental.glare import solar_position

    async with conn() as c, c.cursor() as cur:
        await cur.execute(
            """
            SELECT
                ST_Y(ST_Centroid(geometry)) AS lat,
                ST_X(ST_Centroid(geometry)) AS lon
            FROM road_segments
            WHERE id = %s
            """,
            (segment_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return {}
    lat = float(row[0])
    lon = float(row[1])
    azimuth_deg, elevation_deg = solar_position(lat=lat, lon=lon, at=at)
    return {
        "sun_azimuth_deg": azimuth_deg,
        "sun_elevation_deg": elevation_deg,
    }
