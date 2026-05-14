"""Add ``lane_marking_quality`` to the time-parameterized tile function.

Phase 3 ships the second real sub-score. The tile pipeline gets an
additive attribute: existing columns stay, ``lane_marking_quality``
is appended to the row function's ``RETURNS TABLE``. pg_tileserv
re-discovers the function signature on next request.

Per Phase 2's hand-off and Phase 3.5.7 plan, the rows function and
the bytes function move together — the bytes wrapper selects from
the rows function plus the new column.

The Phase 2.4.8 composite index on ``segment_scores(segment_id,
scoring_run_timestamp)`` continues to power the equality JOIN.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    # PostgreSQL cannot CREATE OR REPLACE FUNCTION when the row type
    # changes (we're adding `lane_marking_quality` to the RETURNS TABLE).
    # Drop both first; idempotent on a fresh DB because IF EXISTS.
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_t("
        "integer, integer, integer, timestamptz);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_t_rows("
        "integer, integer, integer, timestamptz);"
    )
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
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$
            WITH bounds AS (
                SELECT ST_TileEnvelope(z, x, y) AS env_3857
            ),
            snapped AS (
                SELECT
                    CASE
                        WHEN $4 IS NULL THEN NULL
                        WHEN extract(minute FROM $4) >= 30
                            THEN date_trunc('hour', $4) + interval '1 hour'
                        ELSE date_trunc('hour', $4)
                    END AS t_snapped
            )
            SELECT
                rs.id,
                ST_AsMVTGeom(
                    ST_Transform(rs.geometry, 3857),
                    (SELECT env_3857 FROM bounds),
                    4096,
                    256,
                    true
                ) AS geom,
                rs.osm_way_id,
                COALESCE(rs.attrs->>'highway', 'unknown') AS highway,
                ss.sub_score_glare AS glare_score,
                ss.sub_score_lane_marking AS lane_marking_quality,
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
                  AND (
                      ((SELECT t_snapped FROM snapped) IS NOT NULL
                          AND s.scoring_run_timestamp = (SELECT t_snapped FROM snapped))
                      OR (SELECT t_snapped FROM snapped) IS NULL
                  )
                ORDER BY s.inserted_at DESC
                LIMIT 1
            ) ss ON true
            WHERE rs.geometry && ST_Transform((SELECT env_3857 FROM bounds), 4326)
        $$;
        """
    )

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
