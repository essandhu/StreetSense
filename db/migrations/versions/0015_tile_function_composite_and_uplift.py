"""Phase 4: surface composite_risk + propagation_uplift on the tile function.

Migration 0011 split the ``road_segments_tile_t_rows`` plpgsql function
into two index-clean branches (snapped-equality vs. latest-by-inserted).
Phase 4 adds two attributes to the MVT payload:

  - ``composite_risk``: already on the row signature (since 0006), but
    now carries the propagator-aware Phase 4 value instead of the
    Phase 2/3 mean-of-real-sub-scores transitional value. No DDL
    change required for this column.
  - ``propagation_uplift``: **new attribute** the frontend reads to
    distinguish local risk from network amplification. Preserves the
    explainability invariant (CLAUDE.md §"Explainability") — both
    components reach the UI separately, never collapsed.

The function's ``RETURNS TABLE`` shape changes (12 -> 13 columns), so
the existing function must be dropped before re-creation. The two
branches keep the migration-0011 plan shape (composite-index seek for
the snapped-equality case, BTREE-walk for the latest case); only the
projection list is widened.

Per CLAUDE.md, migrations are forward-only.

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    # Drop in correct order: tile_t (calls _rows) before _rows itself.
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_t("
        "integer, integer, integer, timestamptz);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_t_rows("
        "integer, integer, integer, timestamptz);"
    )

    # Re-create the rows function with propagation_uplift added to both
    # branches. The plan shape per branch is unchanged from migration
    # 0011 — we only project an additional column.
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
            propagation_uplift            double precision,
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
                -- (segment_id, scoring_run_timestamp). Plan from
                -- migration 0011 is preserved.
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
                        ss.propagation_uplift,
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
                -- Latest-by-inserted_at branch.
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
                        ss.propagation_uplift,
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

    # Re-create the bytes wrapper with the same column list as the
    # rows function. The MVT layer name stays
    # ``public.road_segments_tile_t`` so frontend layer references
    # don't need to change.
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
                    r.propagation_uplift,
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
    raise NotImplementedError("Migrations are forward-only -- write a new revision.")
