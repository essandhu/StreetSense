"""Phase 5: delta tile function for the GPU-painted delta map layer.

Introduces two new functions next to the existing
``road_segments_tile_t`` (single-run) pipeline:

* ``public.road_segments_tile_delta_rows(z, x, y, run_a, run_b, t)`` —
  table-returning function that joins ``segment_scores`` to itself by
  ``segment_id`` and matching hour-of-day, restricted to the two runs
  the caller picked. Projects per-segment ``composite_delta``,
  ``local_contribution_delta``, ``propagation_uplift_delta``, all four
  ``sub_score_*_delta`` columns, and both runs' scalar ``confidence``.
* ``public.road_segments_tile_delta(z, x, y, run_a, run_b, t)`` —
  ``bytea`` wrapper that ``ST_AsMVT``s the rows function and is what
  pg_tileserv publishes as a tile layer.

Decision (anchored in ``conductor/tracks/phase-5-delta-deployment/
index.md`` under "Discovered during implementation"): a **new**
function rather than parameterizing ``road_segments_tile_t``. The
attribute set is different (deltas, not single-run scores), the
semantic ("compare two runs" vs. "current state") is different,
and pg_tileserv publishes each function as its own layer URL — so
the frontend's delta-layer reference (Task 3.4) maps cleanly to a
distinct tile-source name without parameter dispatch in the SQL.

JOIN shape matches the Task 2.3 / 2.4 repo: equi-join on
``segment_id`` plus ``extract(hour from scoring_run_timestamp)``,
filtered by the two ``scoring_run_id``s and the caller's
``target_hour`` (derived from ``t``; defaults to noon UTC when
``t`` is NULL).

The ``local_contribution_delta`` is computed as ``composite_delta
- propagation_uplift_delta`` so the explainability invariant
(``composite_delta == local_contribution_delta +
propagation_uplift_delta``, from CLAUDE.md §"Explainability") holds
by construction at the SQL boundary — same shape Task 2.4's API
delta route ships.

Per CLAUDE.md, migrations are forward-only.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-16 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    # Defensive drop (these names are new in this migration, but
    # keeping the same idempotent shape as 0015 / 0011 means re-running
    # the migration after a manual edit doesn't surprise the dev).
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_delta("
        "integer, integer, integer, uuid, uuid, timestamptz);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_delta_rows("
        "integer, integer, integer, uuid, uuid, timestamptz);"
    )

    # plpgsql so the hour-resolution branch is explicit. The JOIN
    # below leans on the existing ``segment_scores_run_id_idx`` BTREE
    # to narrow each side to one run's rows, then matches on
    # segment_id (also indexed). The hour-of-day predicate is the
    # follow-up indexed in the track's `index.md` if Task 5.3 surfaces
    # it as the gate.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.road_segments_tile_delta_rows(
            z integer,
            x integer,
            y integer,
            run_a uuid,
            run_b uuid,
            t timestamptz DEFAULT NULL
        )
        RETURNS TABLE (
            id                                   uuid,
            geom                                 geometry(Geometry, 3857),
            osm_way_id                           bigint,
            highway                              text,
            composite_delta                      double precision,
            local_contribution_delta             double precision,
            propagation_uplift_delta             double precision,
            sub_score_lane_marking_delta         double precision,
            sub_score_glare_delta                double precision,
            sub_score_junction_complexity_delta  double precision,
            sub_score_historical_delta           double precision,
            confidence_a                         double precision,
            confidence_b                         double precision
        )
        LANGUAGE plpgsql STABLE PARALLEL SAFE
        AS $$
        DECLARE
            env_3857 geometry := ST_TileEnvelope(z, x, y);
            target_hour integer;
        BEGIN
            IF t IS NULL THEN
                target_hour := 12;  -- Noon UTC default; matches Task 2.4 API route.
            ELSE
                target_hour := extract(hour FROM (t AT TIME ZONE 'UTC'))::integer;
            END IF;

            RETURN QUERY
                SELECT
                    rs.id,
                    ST_AsMVTGeom(
                        ST_Transform(rs.geometry, 3857),
                        env_3857, 4096, 256, true
                    ) AS geom,
                    rs.osm_way_id,
                    COALESCE(rs.attrs->>'highway', 'unknown') AS highway,
                    (sb.composite_risk - sa.composite_risk)                          AS composite_delta,
                    ((sb.composite_risk - sa.composite_risk)
                     - (sb.propagation_uplift - sa.propagation_uplift))              AS local_contribution_delta,
                    (sb.propagation_uplift - sa.propagation_uplift)                  AS propagation_uplift_delta,
                    (sb.sub_score_lane_marking - sa.sub_score_lane_marking)          AS sub_score_lane_marking_delta,
                    (sb.sub_score_glare - sa.sub_score_glare)                        AS sub_score_glare_delta,
                    (sb.sub_score_junction_complexity
                     - sa.sub_score_junction_complexity)                             AS sub_score_junction_complexity_delta,
                    (sb.sub_score_historical - sa.sub_score_historical)              AS sub_score_historical_delta,
                    sa.confidence                                                    AS confidence_a,
                    sb.confidence                                                    AS confidence_b
                FROM road_segments rs
                INNER JOIN segment_scores sa
                    ON sa.segment_id = rs.id
                   AND sa.scoring_run_id = run_a
                   AND extract(hour FROM sa.scoring_run_timestamp) = target_hour
                INNER JOIN segment_scores sb
                    ON sb.segment_id = rs.id
                   AND sb.scoring_run_id = run_b
                   AND extract(hour FROM sb.scoring_run_timestamp) = target_hour
                WHERE rs.geometry && ST_Transform(env_3857, 4326);
        END;
        $$;
        """
    )

    # ``ST_AsMVT`` wrapper. Layer name is
    # ``public.road_segments_tile_delta`` — the same name pg_tileserv
    # publishes the function under, so the MVT layer the frontend
    # subscribes to matches the URL it requests.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.road_segments_tile_delta(
            z integer,
            x integer,
            y integer,
            run_a uuid,
            run_b uuid,
            t timestamptz DEFAULT NULL
        )
        RETURNS bytea
        LANGUAGE sql STABLE PARALLEL SAFE
        AS $$
            SELECT ST_AsMVT(
                features.*,
                'public.road_segments_tile_delta',
                4096,
                'geom'
            )
            FROM (
                SELECT
                    r.id::text AS id,
                    r.geom,
                    r.osm_way_id,
                    r.highway,
                    r.composite_delta,
                    r.local_contribution_delta,
                    r.propagation_uplift_delta,
                    r.sub_score_lane_marking_delta,
                    r.sub_score_glare_delta,
                    r.sub_score_junction_complexity_delta,
                    r.sub_score_historical_delta,
                    r.confidence_a,
                    r.confidence_b
                FROM public.road_segments_tile_delta_rows(z, x, y, run_a, run_b, t) r
                WHERE r.geom IS NOT NULL
            ) features
        $$;
        """
    )

    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.road_segments_tile_delta_rows("
        f"integer, integer, integer, uuid, uuid, timestamptz) TO {APP_ROLE_NAME};"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.road_segments_tile_delta("
        f"integer, integer, integer, uuid, uuid, timestamptz) TO {APP_ROLE_NAME};"
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
