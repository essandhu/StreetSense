"""Phase 4b Task 3.6: city-scope the vector tile functions.

Phase 4b makes every read endpoint city-scoped. The four pg_tileserv
tile functions are the last remaining read surface that still operates
city-blind:

* ``public.road_segments_tile_t_rows(z, x, y, t)``
* ``public.road_segments_tile_t(z, x, y, t)``
* ``public.road_segments_tile_delta_rows(z, x, y, run_a, run_b, t)``
* ``public.road_segments_tile_delta(z, x, y, run_a, run_b, t)``

This migration drops each of the four and re-creates them with a
required ``city_slug text`` parameter inserted **before** the existing
``t`` argument (PostgreSQL requires defaulted parameters to follow
non-defaulted ones — see "Parameter Order" below). The function body
resolves the slug to ``cities.id`` via subquery and filters the
``road_segments`` join by ``city_id``.

The plan parenthetically allows "the equivalent for the chosen tile
server" — pg_tileserv auto-publishes function arguments as URL query
parameters, so the URL shape stays
``/tiles/{function_name}/{z}/{x}/{y}.pbf`` and ``city_slug`` rides
alongside the existing ``t`` / ``run_a`` / ``run_b`` keys. The
``city_slug``-in-URL form is what Phase 4 Task 4.6 reads off the
``activeCity`` Redux slice when it rebinds the MapLibre source.

Parameter Order
---------------

``city_slug`` is **required** (no DEFAULT). Two consequences:

1. Forgetting it in a frontend URL builder fails loudly at the SQL
   layer — same posture as migration 0018's drop of the
   ``city_id`` DEFAULT on the write side. Both invariants ride the
   same rail: writers and readers must thread city explicitly.

2. ``t`` keeps its ``DEFAULT NULL`` so the latest-by-inserted_at
   branch in the rows function still works without a time scrubber
   state. Because PostgreSQL forbids a non-default parameter after a
   defaulted one, the new signature is
   ``(z, x, y, city_slug, t)`` rather than ``(z, x, y, t,
   city_slug)``. The pg_tileserv URL doesn't care — it reads them as
   named query strings — but the SQL grammar does.

Unknown-Slug Semantics
----------------------

The plan demands: *"Tests verify a tile request for a slug outside its
bbox returns an empty MVT, not a 500."*

The rows function resolves the slug with::

    target_city_id := (SELECT id FROM cities WHERE slug = city_slug);

An unknown slug yields ``NULL``. The ``WHERE rs.city_id =
target_city_id`` predicate then evaluates to ``NULL`` for every row
(not TRUE), so the row set is empty. The bytes wrapper's ``ST_AsMVT``
over zero rows returns an empty MVT envelope (or NULL — both are
small and well-formed; the frontend handles either silently). No
PL/pgSQL exception, no 500.

Index Path
----------

Migration 0017 added composite indexes leading with ``city_id`` on
every city-scoped table (e.g.
``segment_scores_city_run_id_idx (city_id, scoring_run_id)`` and
``road_segments_city_osm_way_id_idx (city_id, osm_way_id)``). The
city-filtered query plan walks those indexes for the city slice
before the geometry / time predicates run. No additional index is
needed for this migration.

Grants
------

DROP + CREATE clears any EXECUTE grants on the old signatures. The
new grants re-add EXECUTE for the ``streetsense_app`` role so the
pg_tileserv service account (which connects as the app role per
``docker-compose.yml``) keeps serving tiles.

Per CLAUDE.md, migrations are forward-only.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    # ----- Drop the pre-Phase-4b signatures ------------------------------
    # Drop tile_t (calls _rows) before _rows itself so the dependency
    # chain unwinds cleanly. Argument lists must match the existing
    # signatures from migration 0015 / 0016.
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_t("
        "integer, integer, integer, timestamptz);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_t_rows("
        "integer, integer, integer, timestamptz);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_delta("
        "integer, integer, integer, uuid, uuid, timestamptz);"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.road_segments_tile_delta_rows("
        "integer, integer, integer, uuid, uuid, timestamptz);"
    )

    # ----- road_segments_tile_t_rows (city-scoped) ----------------------
    # Same plpgsql shape as migration 0015 (snapped-equality branch +
    # latest-by-inserted_at branch), plus:
    #
    # 1. Required city_slug parameter immediately after z/x/y. PostgreSQL
    #    forbids a non-defaulted argument after a defaulted one, so
    #    city_slug must precede t.
    # 2. Slug -> city_id resolution via subquery on cities.
    # 3. Additional WHERE predicate filtering rs.city_id by the
    #    resolved id. An unknown slug yields NULL and the predicate
    #    discards every row — empty rowset, no exception.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.road_segments_tile_t_rows(
            z integer,
            x integer,
            y integer,
            city_slug text,
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
            target_city_id uuid := (SELECT c.id FROM cities c WHERE c.slug = city_slug);
        BEGIN
            IF t IS NULL THEN
                t_snapped := NULL;
            ELSIF extract(minute FROM t) >= 30 THEN
                t_snapped := date_trunc('hour', t) + interval '1 hour';
            ELSE
                t_snapped := date_trunc('hour', t);
            END IF;

            IF t_snapped IS NOT NULL THEN
                -- Snapped-equality branch (migration 0011 plan shape).
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
                    WHERE rs.city_id = target_city_id
                      AND rs.geometry && ST_Transform(env_3857, 4326);
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
                    WHERE rs.city_id = target_city_id
                      AND rs.geometry && ST_Transform(env_3857, 4326);
            END IF;
        END;
        $$;
        """
    )

    # Bytes wrapper. The MVT layer name stays
    # 'public.road_segments_tile_t' — same value pg_tileserv publishes
    # the function under — so deck.gl's MVTLayer source-layer name
    # doesn't need to change in the frontend.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.road_segments_tile_t(
            z integer,
            x integer,
            y integer,
            city_slug text,
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
                    r.lane_marking_quality,
                    r.composite_risk,
                    r.propagation_uplift,
                    r.confidence,
                    r.is_stub_lane_marking,
                    r.is_stub_glare,
                    r.is_stub_junction_complexity,
                    r.is_stub_historical
                FROM public.road_segments_tile_t_rows(z, x, y, city_slug, t) r
                WHERE r.geom IS NOT NULL
            ) features
        $$;
        """
    )

    # ----- road_segments_tile_delta_rows (city-scoped) ------------------
    # Same JOIN shape as migration 0016 (INNER JOIN segment_scores twice,
    # filtered by the two scoring_run_ids and the target_hour), plus the
    # city_slug -> target_city_id resolution and the
    # rs.city_id = target_city_id predicate.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.road_segments_tile_delta_rows(
            z integer,
            x integer,
            y integer,
            run_a uuid,
            run_b uuid,
            city_slug text,
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
            target_city_id uuid := (SELECT c.id FROM cities c WHERE c.slug = city_slug);
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
                WHERE rs.city_id = target_city_id
                  AND rs.geometry && ST_Transform(env_3857, 4326);
        END;
        $$;
        """
    )

    # Bytes wrapper for delta. Layer name stays
    # 'public.road_segments_tile_delta' so the frontend layer reference
    # (frontend/src/components/Map/deltaLayer.ts) doesn't move.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.road_segments_tile_delta(
            z integer,
            x integer,
            y integer,
            run_a uuid,
            run_b uuid,
            city_slug text,
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
                FROM public.road_segments_tile_delta_rows(
                    z, x, y, run_a, run_b, city_slug, t
                ) r
                WHERE r.geom IS NOT NULL
            ) features
        $$;
        """
    )

    # ----- Re-grant EXECUTE to the app role ------------------------------
    # DROP cleared the old grants; pg_tileserv's service account
    # connects as streetsense_app and must EXECUTE both wrappers + both
    # rows functions (rows is used by direct callers; bytes is what the
    # tile URL resolves to).
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.road_segments_tile_t_rows("
        f"integer, integer, integer, text, timestamptz) TO {APP_ROLE_NAME};"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.road_segments_tile_t("
        f"integer, integer, integer, text, timestamptz) TO {APP_ROLE_NAME};"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.road_segments_tile_delta_rows("
        f"integer, integer, integer, uuid, uuid, text, timestamptz) TO {APP_ROLE_NAME};"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION public.road_segments_tile_delta("
        f"integer, integer, integer, uuid, uuid, text, timestamptz) TO {APP_ROLE_NAME};"
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
