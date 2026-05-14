"""Split the tile function's LATERAL OR into two index-clean branches.

The Phase 2.4.8 / Phase 3.5.7 ``road_segments_tile_t_rows`` function
embeds a LATERAL subquery whose WHERE clause has the shape::

    WHERE s.segment_id = rs.id
      AND (
          (t_snapped IS NOT NULL AND s.scoring_run_timestamp = t_snapped)
          OR t_snapped IS NULL
      )
    ORDER BY s.inserted_at DESC
    LIMIT 1

PostgreSQL cannot fold this OR at parse time (``t_snapped`` is a
parameter), so it picks the more conservative plan: ``Bitmap Index
Scan`` on ``(segment_id)`` alone, then a ``Filter`` on the OR, then a
``Sort`` of all 24 hourly rows per segment, then ``LIMIT 1``. The
Phase 2.4.8 composite index on ``(segment_id, scoring_run_timestamp)``
is partially wasted.

Phase 3's tile bench measured the regression: warm p99 = 234 ms
(budget 200 ms), against Phase 2.4.8's 128 ms. Splitting the OR into
a dispatch on ``t IS NOT NULL`` lets the planner pick the index seek
for the equality case and the BTREE walk for the NULL case.

This migration switches ``road_segments_tile_t_rows`` from a ``sql``
function to a ``plpgsql`` function with an ``IF``/``ELSE`` on the
snapped value. The bytes wrapper ``road_segments_tile_t`` is also
recreated to keep them in sync (no public-shape change to either).

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_t("
        "integer, integer, integer, timestamptz);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_t_rows("
        "integer, integer, integer, timestamptz);"
    )

    # plpgsql with an explicit IF/ELSE so each branch is a separate
    # query the planner can optimize independently. The two branches
    # share the projection list; only the inner LATERAL changes.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.road_segments_tile_t_rows(
            z integer,
            x integer,
            y integer,
            t timestamptz DEFAULT NULL
        )
        RETURNS TABLE (
            id                            uuid,
            geom                          geometry(Geometry, 3857),
            osm_way_id                    bigint,
            highway                       text,
            glare_score                   double precision,
            lane_marking_quality          double precision,
            composite_risk                double precision,
            confidence                    double precision,
            is_stub_lane_marking          boolean,
            is_stub_glare                 boolean,
            is_stub_junction_complexity   boolean,
            is_stub_historical            boolean,
            scoring_run_timestamp         timestamptz
        )
        LANGUAGE plpgsql STABLE PARALLEL SAFE
        AS $$
        DECLARE
            env_3857 geometry := ST_TileEnvelope(z, x, y);
            t_snapped timestamptz;
        BEGIN
            IF t IS NULL THEN
                t_snapped := NULL;
            ELSIF extract(minute FROM t) >= 30 THEN
                t_snapped := date_trunc('hour', t) + interval '1 hour';
            ELSE
                t_snapped := date_trunc('hour', t);
            END IF;

            IF t_snapped IS NOT NULL THEN
                -- Equality branch: composite-index seek on
                -- (segment_id, scoring_run_timestamp).
                RETURN QUERY
                    SELECT
                        rs.id,
                        ST_AsMVTGeom(
                            ST_Transform(rs.geometry, 3857),
                            env_3857, 4096, 256, true
                        ) AS geom,
                        rs.osm_way_id,
                        COALESCE(rs.attrs->>'highway', 'unknown') AS highway,
                        ss.sub_score_glare,
                        ss.sub_score_lane_marking,
                        ss.composite_risk,
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
                          AND s.scoring_run_timestamp = t_snapped
                        ORDER BY s.inserted_at DESC
                        LIMIT 1
                    ) ss ON true
                    WHERE rs.geometry && ST_Transform(env_3857, 4326);
            ELSE
                -- Latest-by-inserted_at branch: BTREE on segment_id +
                -- per-segment sort.
                RETURN QUERY
                    SELECT
                        rs.id,
                        ST_AsMVTGeom(
                            ST_Transform(rs.geometry, 3857),
                            env_3857, 4096, 256, true
                        ) AS geom,
                        rs.osm_way_id,
                        COALESCE(rs.attrs->>'highway', 'unknown') AS highway,
                        ss.sub_score_glare,
                        ss.sub_score_lane_marking,
                        ss.composite_risk,
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
                        ORDER BY s.inserted_at DESC
                        LIMIT 1
                    ) ss ON true
                    WHERE rs.geometry && ST_Transform(env_3857, 4326);
            END IF;
        END;
        $$;
        """
    )

    # Recreate the bytes wrapper unchanged in shape — just kept in
    # sync with the rows function for clarity.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.road_segments_tile_t(
            z integer, x integer, y integer, t timestamptz DEFAULT NULL
        )
        RETURNS bytea
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$
            SELECT ST_AsMVT(features.*, 'public.road_segments_tile_t', 4096, 'geom')
            FROM (
                SELECT
                    r.id::text AS id,
                    r.geom,
                    r.osm_way_id,
                    r.highway,
                    r.glare_score,
                    r.lane_marking_quality,
                    r.composite_risk,
                    r.confidence,
                    r.is_stub_lane_marking,
                    r.is_stub_glare,
                    r.is_stub_junction_complexity,
                    r.is_stub_historical
                FROM public.road_segments_tile_t_rows(z, x, y, t) r
                WHERE r.geom IS NOT NULL
            ) features
        $$;
        """
    )

    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.road_segments_tile_t_rows(integer, integer, integer, timestamptz) TO {APP_ROLE_NAME};"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.road_segments_tile_t(integer, integer, integer, timestamptz) TO {APP_ROLE_NAME};"
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
