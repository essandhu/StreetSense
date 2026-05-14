"""Add incidents table for the historical-correlation scorer.

Phase 4.5 ingests reported road incidents (geocoded crashes + injuries)
from the dataset chosen by ADR 0007 (MassDOT IMPACT, tentative). The
``HistoricalCorrelationScorer`` reads from this table and computes a
per-segment kernel-density-estimated proximity score weighted by
recency.

Schema notes:

- ``geom`` is PostGIS Point in WGS84 (SRID 4326). GIST-indexed for
  spatial KDE queries (``ST_DWithin``, ``ST_Distance``).
- ``incident_at`` is timestamptz; BTREE-indexed for recency-window
  filtering and for ``max(incident_at)`` queries on
  ``/admin/freshness``.
- ``(provider, provider_incident_id)`` is the natural key for
  idempotent re-ingestion (``INSERT ... ON CONFLICT DO NOTHING``).
- ``severity`` is text (mapped from ``IncidentSeverity`` StrEnum in
  ``ingestion/incidents/provider.py``). Constrained to the four
  values the enum defines.
- ``metadata`` is JSONB so providers can ship richer fields (officer
  narrative, vehicle counts, contributing factors) without a
  migration.

Granted: SELECT + INSERT to the application role. UPDATE/DELETE are
withheld -- re-ingestion uses INSERT ON CONFLICT for idempotency; any
mutation of prior rows requires an explicit later migration, mirroring
the append-only posture of ``segment_imagery`` (migration 0008).

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE incidents (
            id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            provider                text NOT NULL,
            provider_incident_id    text NOT NULL,
            geom                    geometry(Point, 4326) NOT NULL,
            incident_at             timestamptz NOT NULL,
            severity                text NOT NULL,
            metadata                jsonb NOT NULL DEFAULT '{}'::jsonb,
            ingested_at             timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT incidents_natural_key
                UNIQUE (provider, provider_incident_id),
            CONSTRAINT incidents_severity_enum
                CHECK (severity IN ('fatal', 'injury', 'property_damage_only', 'unknown'))
        );
        """
    )

    op.execute(
        """
        CREATE INDEX incidents_geom_idx ON incidents USING GIST (geom);
        """
    )
    op.execute(
        """
        CREATE INDEX incidents_incident_at_idx ON incidents (incident_at);
        """
    )

    op.execute(f"GRANT SELECT, INSERT ON incidents TO {APP_ROLE_NAME};")


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only -- write a new revision.")
