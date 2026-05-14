"""Register `imagery` as a data source.

Phase 3 introduces Mapillary-sourced street-level imagery (ADR 0005).
``/admin/freshness`` reports the latest ``capture_date`` observed across
``segment_imagery``; the row inserted here gives the endpoint a stable
entry to look up. ``last_ingested_at`` is set to the migration time and
will be bumped by the imagery ingestion job (Task 3.2.7) on each
successful run via ``UPDATE data_sources``.

The seed metadata records the provider and the ADR for traceability.
``perception_model`` is a separate data source — registered when the
chosen artifact is uploaded to MinIO (Task 3.3.7), not here.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # `DO UPDATE` rather than `DO NOTHING`: pre-existing `imagery` rows in
    # this DB may come from Phase 2 test fixtures that inserted a
    # placeholder before this migration owned the seed. The migration
    # runs exactly once per environment (alembic_version tracks), so
    # overwriting metadata here is correct first-apply behavior. The
    # ``last_ingested_at`` is set to ``now()`` only when no prior value
    # exists; the ingestion job (Task 3.2.7) takes over from there.
    op.execute(
        """
        INSERT INTO data_sources (name, last_ingested_at, metadata)
        VALUES (
            'imagery',
            now(),
            jsonb_build_object(
                'kind', 'fetch',
                'provider', 'mapillary',
                'adr', '0005-imagery-provider',
                'license', 'CC-BY-SA'
            )
        )
        ON CONFLICT (name) DO UPDATE
        SET metadata = EXCLUDED.metadata,
            last_ingested_at = COALESCE(data_sources.last_ingested_at, EXCLUDED.last_ingested_at)
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
