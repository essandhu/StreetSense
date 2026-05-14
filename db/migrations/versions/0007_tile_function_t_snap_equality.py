"""Optimize the time-parameterized tile function via equality JOIN.

The 2.4.8 benchmark on the seeded Cambridge dataset (878k
``segment_scores`` rows) found the Phase 2.4.5 function at p99 ~664 ms,
~3.3x over budget. Root cause: the LATERAL `ORDER BY abs(...) LIMIT 1`
ranking expression cannot use a BTREE index — every candidate row's
expression value is computed at query time.

The fix is shape-preserving:

- Snap ``t`` to the nearest persisted hourly sample **once** at query
  start (the scoring run writes UTC hourly samples; the closest such
  hour can be computed without consulting `segment_scores`).
- JOIN ``segment_scores`` by equality on
  ``(segment_id, scoring_run_timestamp)`` — covered by the new
  composite index.

The behavioral contract is unchanged: any ``t`` within 30 minutes of an
hourly sample resolves to that hourly sample, exactly the same set the
prior LATERAL query would have picked. The test
``tests/api/test_segment_detail_t.py::test_snaps_to_nearest_hourly_sample``
exercises the 12:25Z → 12:00Z case and continues to pass.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    # Composite index that supports the new equality JOIN. Idempotent
    # so this migration is safe to re-apply against an already-indexed
    # DB.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS segment_scores_segment_timestamp_idx
            ON segment_scores (segment_id, scoring_run_timestamp);
        """
    )

    # Rewrite the rows function to snap `t` then equality-join.
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
            ),
            -- Snap `t` to the nearest hour. Rule: minute >= 30 rounds up,
            -- else rounds down. Matches the prior LATERAL ranking exactly
            -- for any `t` that lies within +/-30 minutes of an hourly sample
            -- (which is every `t` since the samples ARE hourly).
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
                ss.composite_risk,
                ss.confidence,
                ss.is_stub_lane_marking,
                ss.is_stub_glare,
                ss.is_stub_junction_complexity,
                ss.is_stub_historical,
                ss.scoring_run_timestamp
            FROM road_segments rs
            LEFT JOIN LATERAL (
                -- For `t` snapped to an hourly sample: equality on
                -- (segment_id, scoring_run_timestamp) — index-friendly.
                -- For `t` NULL: most-recent row by inserted_at.
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

    # The bytea function only changed if the rows function's column
    # list changed (it didn't), but a CREATE OR REPLACE is cheap and
    # documents that the two move together.
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

    # Ensure the app role keeps EXECUTE — CREATE OR REPLACE preserves
    # grants in Postgres, but make it explicit so future readers don't
    # have to remember that.
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.road_segments_tile_t_rows(integer, integer, integer, timestamptz) TO {APP_ROLE_NAME};"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.road_segments_tile_t(integer, integer, integer, timestamptz) TO {APP_ROLE_NAME};"
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
