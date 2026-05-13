"""Add data_sources table.

Tracks the latest successful ingestion per registered data source. Consumed
by `/admin/freshness` (Phase 1.5). Phase 1 registers one source (`osm`); the
shape supports more without breaking changes.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-13 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data_sources (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name              text NOT NULL,
            last_ingested_at  timestamptz,
            metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at        timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE UNIQUE INDEX data_sources_name_uidx ON data_sources (name);")

    # The app role reads + writes (ingestion updates last_ingested_at on every
    # successful run). Mutation here is allowed; this is not an append-only table.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON data_sources TO {APP_ROLE_NAME};")


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
