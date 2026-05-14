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
    op.execute(
        """
        INSERT INTO data_sources (name, description, license_url)
        VALUES
            (
                'incidents',
                'Historical road incidents (crashes, injuries) for the configured city. '
                'Provider chosen by ADR 0007 (MassDOT IMPACT, tentative pending live evaluation). '
                'Persisted in the `incidents` table; queryable via `max(incident_at)`.',
                'https://apps.impact.dot.state.ma.us/'
            ),
            (
                'propagation_algorithm',
                'C++ Network Risk Propagator algorithm version. Read live from '
                '`streetsense_propagator.version` + the registered strategy''s '
                '`version()`. Algorithm chosen by ADR 0006 (Phase 4.8.2).',
                'https://www.boost.org/doc/libs/1_81_0/libs/graph/doc/index.html'
            )
        ON CONFLICT (name) DO NOTHING;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only -- write a new revision.")
