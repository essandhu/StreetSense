"""Add `road_segments_tile` view for pg_tileserv.

The view exposes:

- `id` (UUID) — joined back to road_segments for click-through.
- `geometry` (MVT-friendly LineString in 4326).
- `osm_way_id`, `highway` — useful per-tile properties.
- `risk_stub_bucket` (0-4) — a deterministic 5-step value computed via
  PostgreSQL's `hashtext()` so the GPU-side Mapbox color expression can
  pick a palette entry without per-frame JS or per-request Python.

This is the *publishing* surface for tiles. road_segments stays as the
authoritative table; the view layers presentation concerns on top.

The Python `stub_risk` function in api/scoring_stub.py uses xxhash and
serves /segments/{id} composite/sub-scores. The SQL `risk_stub_bucket`
serves the visual palette only — both are stubs, both are deterministic;
they intentionally do **not** need to agree numerically (Phase 1 has no
real risk to be agree about).

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    # The VIEW is intentionally simple — pg_tileserv handles MVT encoding.
    # We expose `geometry` directly; pg_tileserv will pass it through ST_AsMVTGeom.
    op.execute(
        """
        CREATE OR REPLACE VIEW road_segments_tile AS
        SELECT
            rs.id,
            rs.geometry,
            rs.osm_way_id,
            COALESCE(rs.attrs->>'highway', 'unknown') AS highway,
            mod(abs(hashtext(rs.id::text)), 5)        AS risk_stub_bucket
        FROM road_segments rs;
        """
    )

    # The view inherits SELECT permissions from its underlying table for the
    # owner role, but pg_tileserv connects as the app role — grant
    # explicitly.
    op.execute(f"GRANT SELECT ON road_segments_tile TO {APP_ROLE_NAME};")


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
