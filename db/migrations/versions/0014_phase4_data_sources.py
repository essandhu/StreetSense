"""Register Phase 4 data sources: incidents + propagation_algorithm.

The ``/admin/freshness`` endpoint reports per-source freshness against
the ``data_sources`` registry table (migration 0002 onward). Phase 4
adds two sources:

- ``incidents``: latest ``max(incident_at)`` from the ``incidents``
  table (populated by the MassDOT IMPACT adapter, per ADR 0007).
- ``propagation_algorithm``: current C++ engine algorithm version +
  build timestamp. Read from ``streetsense_propagator.version`` at
  endpoint-call time; the row here records the *registry entry* so
  the admin endpoint knows to surface it.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # `metadata` is the JSONB column on data_sources (migration 0002).
    # The ``/admin/freshness`` endpoint reads ``name`` + ``last_ingested_at``
    # + selected metadata keys; the keys here mirror the conventions
    # established by migrations 0005 (solar_position) and 0009 (imagery):
    # ``kind`` classifies the source (``fetch`` / ``compute`` / ``model``)
    # and ``adr`` cites the governing decision.
    op.execute(
        """
        INSERT INTO data_sources (name, last_ingested_at, metadata)
        VALUES (
            'incidents',
            NULL,
            jsonb_build_object(
                'kind', 'fetch',
                'provider', 'massdot-impact',
                'adr', '0007-incident-dataset',
                'license', 'public-records',
                'description',
                'Historical road incidents (crashes, injuries) for the configured city.'
            )
        )
        ON CONFLICT (name) DO NOTHING;
        """
    )

    op.execute(
        """
        INSERT INTO data_sources (name, last_ingested_at, metadata)
        VALUES (
            'propagation_algorithm',
            now(),
            jsonb_build_object(
                'kind', 'compute',
                'adr', '0006-propagation-algorithm',
                'library', 'streetsense_propagator',
                'algorithm', 'draft',
                'description',
                'C++ Network Risk Propagator algorithm. Algorithm chosen by ADR 0006 (Phase 4.8.2).'
            )
        )
        ON CONFLICT (name) DO NOTHING;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only -- write a new revision.")
