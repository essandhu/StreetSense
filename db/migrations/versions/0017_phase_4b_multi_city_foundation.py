"""Phase 4b: multi-city foundation.

Lands the schema half of `phase-4b-multi-city`: a `cities` table, the
`cambridge` bootstrap row that the existing single-city data backfills
to, and a `city_id` foreign key on every spatial / score table.

The architecture has always required cities to be a configuration
concern, not a code concern (CLAUDE.md Extension Point 2). Through
Phase 4 the schema and code implicitly assumed one city. This
migration makes the city dimension real:

1. ``cities`` table — UUID PK, lowercase slug (UNIQUE NOT NULL),
   display name, bbox as ``geometry(Polygon, 4326)`` with a GIST index,
   ``default_zoom``, IANA ``timezone``, audit timestamps. Curated set
   is enumerated in ADR 0010.

2. ``cambridge`` bootstrap row — inserted directly here so the
   subsequent backfill UPDATE has a valid target. The other four
   curated cities (Phoenix, San Francisco, Austin, Los Angeles) are
   seeded by ``make seed-cities`` from
   ``config/cities/{slug}.yaml`` (Phase 4b Task 1.6).

3. ``city_id`` on every spatial / score table:
   ``road_segments``, ``scoring_runs``, ``segment_scores``,
   ``segment_imagery``, ``incidents``. Each gets:
   - ``city_id UUID NOT NULL REFERENCES cities(id)`` — added in three
     SQL steps (ADD nullable → UPDATE backfill → SET NOT NULL → ADD
     FK) so existing rows are tagged with ``cambridge.id`` before the
     constraint trips.
   - A supporting composite index leading with ``city_id`` so the
     city-scoped read path is index-supported without sequential
     scans.

4. App-role grants on the new table — ``SELECT, INSERT`` only (cities
   are curated, not user-mutated). The existing ``REVOKE`` posture on
   ``scoring_runs`` and ``segment_scores`` is untouched; this is a
   column-add, not a privilege change.

The shipped set is *five* cities, not four (ADR 0010): cambridge is
retained as a grandfathered demo city alongside the four curated
additions. AC-1 still satisfied (target was 3-4, we ship 5). Reason
captured in the track's index.md "Discoveries" section.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# --- Alembic identifiers --------------------------------------------------
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"

# Tables that gain city_id. Order matters only for human readability —
# each table's column-add is independent. The values are
# (table_name, composite_index_name, composite_index_columns_sql).
# A single-column index on city_id alone is insufficient: most of these
# tables already have an existing index on the second column (segment_id
# / scoring_run_id / timestamp), and the composite both supports
# city-scoped reads and avoids index-bloat by replacing the city_id-only
# variant.
_CITY_SCOPED_TABLES: tuple[tuple[str, str, str], ...] = (
    # road_segments: city + OSM way ID supports per-city OSM lookups.
    # The geometry GIST index already covers spatial reads.
    ("road_segments", "road_segments_city_osm_way_id_idx", "(city_id, osm_way_id)"),
    # scoring_runs: city + timestamp DESC supports "latest run for city".
    (
        "scoring_runs",
        "scoring_runs_city_timestamp_idx",
        "(city_id, scoring_run_timestamp DESC)",
    ),
    # segment_scores: city + scoring_run_id supports "all scores for a
    # run, for this city" — the dominant API read shape.
    (
        "segment_scores",
        "segment_scores_city_run_id_idx",
        "(city_id, scoring_run_id)",
    ),
    # segment_imagery: city + segment_id supports per-city perception
    # scans (the existing (segment_id, sample_index) index is left in
    # place for per-segment iteration).
    (
        "segment_imagery",
        "segment_imagery_city_segment_idx",
        "(city_id, segment_id)",
    ),
    # incidents: city + incident_at supports recent-incidents-by-city
    # queries (the existing GIST(geom) and BTREE(incident_at) stay).
    (
        "incidents",
        "incidents_city_incident_at_idx",
        "(city_id, incident_at)",
    ),
)


def upgrade() -> None:
    # ----- cities table --------------------------------------------------
    op.execute(
        """
        CREATE TABLE cities (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            slug          text NOT NULL,
            name          text NOT NULL,
            bbox          geometry(Polygon, 4326) NOT NULL,
            default_zoom  integer NOT NULL,
            timezone      text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE UNIQUE INDEX cities_slug_uidx ON cities (slug);")
    op.execute("CREATE INDEX cities_bbox_gist ON cities USING GIST (bbox);")

    # ----- Transitional default-city resolver ----------------------------
    # Phase 4b lands the city_id NOT NULL columns *before* Phase 2's
    # ingestion refactor parameterizes every writer by --city <slug>.
    # To avoid an 18-file write-site cascade in this one commit, we
    # install a SQL function that returns the cambridge city_id and
    # use it as the column-level DEFAULT on each city_id column.
    #
    # The function is STABLE (returns the same value within a single
    # statement) and lookup-by-slug ensures it tracks the cities table
    # rather than a baked-in literal UUID.
    #
    # Precedent: migration 0012 used the same ADD-with-DEFAULT pattern
    # for propagation_uplift and dropped the default once writers were
    # updated. Phase 2's migration will:
    #   1. ALTER COLUMN city_id DROP DEFAULT on every city-scoped table.
    #   2. DROP FUNCTION default_cambridge_city_id();
    # …after all writers explicitly supply city_id (Phase 2 plan, Tasks
    # 2.2 - 2.4).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION default_cambridge_city_id()
        RETURNS uuid
        LANGUAGE sql
        STABLE
        AS $$
            SELECT id FROM cities WHERE slug = 'cambridge'
        $$;
        """
    )

    # ----- cambridge bootstrap row ---------------------------------------
    # bbox matches config/cities/cambridge.yaml as of Phase 1; the
    # seed-cities loader (Task 1.6) will reconcile this row with the
    # YAML on every run via UPSERT, so any future bbox tweak in the
    # YAML propagates without a new migration. Display name and zoom
    # are sourced from the same file's display_name + a sensible
    # Phase 1 default (12 for a metro that fits in a single MapLibre
    # frame at city-zoom).
    op.execute(
        """
        INSERT INTO cities (slug, name, bbox, default_zoom, timezone)
        VALUES (
            'cambridge',
            'Cambridge, MA',
            ST_MakeEnvelope(-71.16, 42.35, -71.07, 42.41, 4326),
            12,
            'America/New_York'
        );
        """
    )

    # ----- Per-table city_id column + backfill + FK + index --------------
    for table_name, index_name, index_cols_sql in _CITY_SCOPED_TABLES:
        # 1. Add nullable so existing rows survive the ALTER, with the
        #    transitional default so omitted-city_id inserts continue to
        #    work through Phase 2's writer refactor.
        op.execute(
            f"""
            ALTER TABLE {table_name}
                ADD COLUMN city_id uuid DEFAULT default_cambridge_city_id();
            """
        )
        # 2. Backfill — every pre-Phase-4b row resolves to cambridge.
        op.execute(
            f"""
            UPDATE {table_name}
            SET city_id = default_cambridge_city_id()
            WHERE city_id IS NULL;
            """
        )
        # 3. Tighten to NOT NULL — fails loudly if backfill missed
        #    anything (should not happen by construction, but the
        #    constraint is the safety net). The DEFAULT remains so
        #    INSERT statements that omit city_id resolve to cambridge
        #    rather than tripping NOT NULL. Phase 2 drops the DEFAULTs
        #    after writers are parameterized by --city <slug>.
        op.execute(f"ALTER TABLE {table_name} ALTER COLUMN city_id SET NOT NULL;")
        # 4. FK to cities — added after backfill so no row is dangling
        #    at constraint-creation time. NO ACTION on delete is the
        #    default and is correct: dropping a city should fail loudly
        #    if scores reference it; resolution is operational, not
        #    cascading.
        op.execute(
            f"""
            ALTER TABLE {table_name}
                ADD CONSTRAINT {table_name}_city_id_fkey
                FOREIGN KEY (city_id) REFERENCES cities(id);
            """
        )
        # 5. Composite index leading with city_id — supports the
        #    city-scoped read path without forcing index intersection.
        op.execute(f"CREATE INDEX {index_name} ON {table_name} {index_cols_sql};")

    # ----- App-role grants on the new cities table -----------------------
    # SELECT for the frontend / API listing endpoint; INSERT so
    # `make seed-cities` running as the app role can upsert the curated
    # set. UPDATE is needed so the seeder can refresh bbox/timezone/
    # default_zoom when a city's YAML changes (the seeder upserts; the
    # `cities` table is curated, not append-only). DELETE is withheld
    # — cities are never removed from the shipped set without a
    # follow-up ADR.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON cities TO {APP_ROLE_NAME};")


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
