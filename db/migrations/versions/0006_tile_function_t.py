"""Time-parameterized tile function for pg_tileserv.

Phase 2 ships a vector-tile source that varies by time: per-tile, each
``road_segments`` feature carries the ``glare_score`` and four
``is_stub_*`` flags from the ``segment_scores`` row whose
``scoring_run_timestamp`` is nearest to the requested ``t``. The
endpoint snaps to the nearest persisted hourly sample, so the front-end
can scrub through the 24 samples without any continuous interpolation.

Two functions ship together:

- ``road_segments_tile_t_rows(z, x, y, t)`` returns the per-feature row
  set the MVT is built from. Test-friendly — the inner SQL is callable
  directly and the column set is inspectable. pg_tileserv exposes
  table-returning functions as JSON endpoints too, which is harmless.
- ``road_segments_tile_t(z, x, y, t)`` returns ``bytea`` — pg_tileserv's
  function-tile-source contract. Internally it calls
  ``ST_AsMVT(...)`` over the rows function.

The split keeps the MVT encoding concerns separate from the
data-shape concerns, which the test exercises via direct SQL.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    # --- Inner rows function ----------------------------------------------
    # Returns the per-feature row set inside the tile envelope. Each
    # feature is joined LATERALly against the segment_scores row whose
    # scoring_run_timestamp is nearest to `t`. When `t` is NULL we fall
    # back to the most recent inserted row (Phase 1 behavior).
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
                ORDER BY
                    CASE WHEN t IS NULL THEN 0 ELSE 1 END,
                    CASE WHEN t IS NULL THEN s.inserted_at END DESC,
                    CASE WHEN t IS NULL THEN NULL
                         ELSE abs(extract(epoch FROM (s.scoring_run_timestamp - t)))
                    END ASC
                LIMIT 1
            ) ss ON true
            WHERE rs.geometry && ST_Transform((SELECT env_3857 FROM bounds), 4326)
        $$;
        """
    )

    # --- MVT bytes function ----------------------------------------------
    # The actual function-tile-source pg_tileserv serves. Filters out
    # features that clipped to an empty geometry (ST_AsMVTGeom returns
    # NULL for those).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.road_segments_tile_t(
            z integer,
            x integer,
            y integer,
            t timestamptz DEFAULT NULL
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
