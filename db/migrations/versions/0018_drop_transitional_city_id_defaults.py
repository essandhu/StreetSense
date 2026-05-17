"""Phase 4b finale: drop the transitional city_id DEFAULTs.

Migration 0017 added ``city_id`` to every spatial / score table with a
transitional DEFAULT pointing at the cambridge city_id (via the
``default_cambridge_city_id()`` SQL function). The default let the
Phase 1-4 writers continue to work unchanged through Phase 4b's
Phase 1 schema work.

Phase 4b's Phase 2 refactor (Tasks 2.1-2.4) parameterizes every writer
by ``--city <slug>`` so they now supply ``city_id`` explicitly. With
all writers updated, the DEFAULT becomes a footgun: it lets a future
caller silently mis-attribute data to cambridge. This migration drops
it.

Concretely:

1. ``ALTER COLUMN city_id DROP DEFAULT`` on every city-scoped table:
   road_segments, scoring_runs, segment_scores, segment_imagery,
   incidents. The columns stay ``NOT NULL`` — any future INSERT that
   omits city_id will now fail loudly at the schema layer.

2. ``DROP FUNCTION default_cambridge_city_id()``. The function existed
   only to back the column-level defaults; with no callers it's dead
   code.

Precedent: migration 0012 used the same ADD-WITH-DEFAULT then
DROP-DEFAULT pattern for ``propagation_uplift``. The pattern is
deliberate: a schema invariant (NOT NULL) lands first; an
implementation transition (writer refactor) follows; the final
hardening (drop the default) closes the loop.

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


_CITY_SCOPED_TABLES: tuple[str, ...] = (
    "road_segments",
    "scoring_runs",
    "segment_scores",
    "segment_imagery",
    "incidents",
)


def upgrade() -> None:
    for table_name in _CITY_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table_name} ALTER COLUMN city_id DROP DEFAULT;")

    # Drop the now-unused helper function. If a future track needs
    # cambridge's UUID, it should look it up by slug via
    # ``ingestion.seed_cities.get_city_id_by_slug`` rather than
    # depend on a SQL-level shortcut.
    op.execute("DROP FUNCTION IF EXISTS default_cambridge_city_id();")


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
