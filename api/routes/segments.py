"""GET /segments/{id} — per-segment detail with stub sub-scores."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from api.db import conn
from api.schemas import SegmentDetail, SubScores
from api.scoring_stub import stub_risk

router = APIRouter(prefix="/segments", tags=["segments"])


@router.get("/{segment_id}", response_model=SegmentDetail)
async def get_segment(segment_id: UUID) -> SegmentDetail:
    """Return the per-segment detail payload.

    Phase 1: `risk_stub` is always True. The shape is stable across phases —
    Phase 2/3/4 fill in real values without breaking changes.
    """
    async with conn() as c, c.cursor() as cur:
        await cur.execute(
            "SELECT id, osm_way_id, attrs FROM road_segments WHERE id = %s",
            (segment_id,),
        )
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"segment {segment_id} not found")

    seg_id, osm_way_id, attrs = row
    score = stub_risk(seg_id)

    return SegmentDetail(
        segment_id=seg_id,
        osm_way_id=osm_way_id,
        composite_risk=score.composite,
        sub_scores=SubScores(
            lane_marking_quality=score.sub_scores.lane_marking_quality,
            glare_exposure=score.sub_scores.glare_exposure,
            junction_complexity=score.sub_scores.junction_complexity,
            historical_correlation=score.sub_scores.historical_correlation,
        ),
        confidence=score.confidence,
        risk_stub=score.risk_stub,
        attrs=attrs or {},
    )
