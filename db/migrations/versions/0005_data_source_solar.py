"""Register `solar_position` as a data source.

Phase 2 introduces the first compute-driven data source: `pvlib`'s NREL
SPA solar-position model. Recording it in `data_sources` lets
`/admin/freshness` report on its presence the same way it reports on
OSM, even though there's nothing to "ingest" — the upstream is a
library version, not a file.

`last_ingested_at` is set to the migration time (a one-time "first
seen" stamp). Subsequent re-runs are not expected to bump this row,
because the upstream is the library version pinned in `pyproject.toml`.
If `pvlib` is upgraded, a follow-up migration may update the metadata.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # `metadata->>'library'` documents the wrapped library; `model` names
    # the algorithm. Both are advisory; the consumer is /admin/freshness.
    op.execute(
        """
        INSERT INTO data_sources (name, last_ingested_at, metadata)
        VALUES (
            'solar_position',
            now(),
            jsonb_build_object(
                'kind', 'compute',
                'library', 'pvlib',
                'model', 'nrel-spa',
                'adr', '0003-solar-position-library'
            )
        )
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
