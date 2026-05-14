"""GET /segments/{id} — per-segment detail with per-sub-score is_stub flags.

Phase 2 changes (breaking):

- Top-level ``risk_stub`` flag is removed. Per-sub-score ``is_stub``
  flags inside ``sub_scores`` replace it.
- Accepts an optional ``t`` ISO-8601 UTC query parameter and snaps to
  the nearest persisted hourly sample in ``segment_scores``.
- Returns the glare scorer's real value plus its azimuth / elevation
  metadata in ``sub_scores.glare_exposure``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from api.db import conn
from api.schemas import SegmentDetail, SubScore, SubScores
from api.scoring_stub import stub_risk

router = APIRouter(prefix="/segments", tags=["segments"])


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


_SELECT_SNAPPED_SCORE_SQL = """
SELECT
    rs.id            AS segment_id,
    rs.osm_way_id    AS osm_way_id,
    rs.attrs         AS attrs,
    ss.composite_risk,
    ss.sub_score_lane_marking,
    ss.sub_score_glare,
    ss.sub_score_junction_complexity,
    ss.sub_score_historical,
    ss.confidence,
    ss.is_stub_lane_marking,
    ss.is_stub_glare,
    ss.is_stub_junction_complexity,
    ss.is_stub_historical,
    ss.scoring_run_timestamp
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
WHERE rs.id = %(id)s
"""


@router.get("/{segment_id}", response_model=SegmentDetail)
async def get_segment(
    segment_id: UUID,
    t: datetime | None = Query(  # noqa: B008 - FastAPI's Query() as default is the idiomatic pattern
        default=None,
        description=(
            "Optional ISO-8601 UTC instant. Snaps to the nearest persisted "
            "hourly sample in segment_scores. Omitted ⇒ most recent score row."
        ),
    ),
) -> SegmentDetail:
    """Return the per-segment detail payload.

    Per-sub-score `is_stub` flags expose which scorers are real:
    glare flips to false in Phase 2; the other three remain true.
    """
    t_param: datetime | None = None
    if t is not None:
        t_param = t if t.tzinfo is not None else t.replace(tzinfo=UTC)

    async with conn() as c, c.cursor() as cur:
        await cur.execute(_SELECT_SNAPPED_SCORE_SQL, {"id": segment_id, "t": t_param})
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"segment {segment_id} not found")

    (
        seg_id,
        osm_way_id,
        attrs,
        composite_risk,
        sub_lane,
        sub_glare,
        sub_junction,
        sub_historical,
        confidence,
        is_stub_lane,
        is_stub_glare,
        is_stub_junction,
        is_stub_historical,
        scoring_run_timestamp,
    ) = row

    if composite_risk is None:
        # No `segment_scores` row exists for this segment yet (e.g., the
        # scoring run hasn't been executed since this segment was ingested).
        # Fall back to the Phase 1 stub so the endpoint stays useful for
        # newly-ingested segments before the next scoring run.
        stub = stub_risk(seg_id)
        return SegmentDetail(
            segment_id=seg_id,
            osm_way_id=osm_way_id,
            composite_risk=stub.composite,
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
            confidence=stub.confidence,
            attrs=attrs or {},
        )

    # Recompute glare metadata for the returned row. The scorer is
    # pure-functional and cheap; this avoids persisting the metadata
    # JSON in storage just to surface it at the API.
    glare_metadata: dict[str, Any] = {}
    if not is_stub_glare and scoring_run_timestamp is not None:
        glare_metadata = await _compute_glare_metadata(seg_id, scoring_run_timestamp)

    return SegmentDetail(
        segment_id=seg_id,
        osm_way_id=osm_way_id,
        composite_risk=float(composite_risk),
        sub_scores=SubScores(
            lane_marking_quality=_build_subscore(
                None if sub_lane is None else float(sub_lane),
                is_stub=bool(is_stub_lane),
                confidence=0.0,
            ),
            glare_exposure=_build_subscore(
                None if sub_glare is None else float(sub_glare),
                is_stub=bool(is_stub_glare),
                confidence=float(confidence) if confidence is not None else 0.0,
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
        confidence=float(confidence) if confidence is not None else 0.0,
        attrs=attrs or {},
    )


async def _compute_glare_metadata(segment_id: UUID, at: datetime) -> dict[str, Any]:
    """Compute the glare metadata (sun_azimuth_deg, sun_elevation_deg) for
    the segment's representative point at ``at``.

    Done here, not in storage, because (a) the underlying geometry might
    rotate between scoring runs (a re-ingested OSM way), and (b) it
    keeps `segment_scores` small. The scorer is pure-functional and
    fast enough at API latency.
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
