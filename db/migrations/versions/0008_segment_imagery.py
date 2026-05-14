"""Add segment_imagery table.

Phase 3 ingestion stores one row per piece of street-level imagery the
provider returned for a segment's waypoints. The bytes live in MinIO
under ``streetsense-imagery/<provider>/<provider_image_id>.<ext>``;
this table is the metadata index so the perception scorer can locate
imagery without round-tripping to the provider.

Schema notes:

- No geometry column. The spatial key is ``segment_id`` (FK to
  ``road_segments``), which itself has a GIST-indexed LineString.
  Querying imagery by space goes ``ST_DWithin(... road_segments ...)`` →
  join → ``segment_imagery``.
- The natural key for upserts is the composite
  ``(provider, provider_image_id, segment_id)`` so a given upstream
  image attached to multiple nearby segments stays one row per
  segment (the perception scorer aggregates per-segment, so a shared
  image scoring twice is acceptable). Enforced as a UNIQUE
  constraint.
- ``camera_params`` is JSONB so each provider can ship its own
  intrinsics shape without a migration.
- ``ingested_at`` is the StreetSense row-creation time; the
  provider's capture timestamp is ``capture_date``.

The application role gets ``SELECT, INSERT`` only; updates land via
new ``INSERT ON CONFLICT`` upserts when a re-ingestion finds the same
``(provider, provider_image_id, segment_id)`` and wants to refresh
``camera_params`` or ``heading_deg``. To keep the table close to
append-only and consistent with the existing scoring tables' posture,
``UPDATE`` and ``DELETE`` are **not** granted to the app role here.
Re-ingestion changes that need to mutate prior rows must come in a
later, explicit migration.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


APP_ROLE_NAME = "streetsense_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE segment_imagery (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            segment_id          uuid NOT NULL
                                  REFERENCES road_segments(id) ON DELETE CASCADE,
            provider            text NOT NULL,
            provider_image_id   text NOT NULL,
            sample_index        integer NOT NULL,
            capture_date        date NOT NULL,
            heading_deg         double precision NOT NULL,
            camera_params       jsonb NOT NULL DEFAULT '{}'::jsonb,
            object_key          text NOT NULL,
            ingested_at         timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT segment_imagery_natural_key
                UNIQUE (provider, provider_image_id, segment_id),
            CONSTRAINT segment_imagery_heading_range
                CHECK (heading_deg >= 0.0 AND heading_deg < 360.0),
            CONSTRAINT segment_imagery_sample_index_nonneg
                CHECK (sample_index >= 0)
        );
        """
    )

    # Lookup by `(segment_id, sample_index)` powers the perception
    # scorer's per-segment iteration and the segment-detail handler's
    # imagery-strip render. Capture-date indexed for the
    # /admin/freshness endpoint's `max(capture_date)` query.
    op.execute(
        """
        CREATE INDEX segment_imagery_segment_sample_idx
            ON segment_imagery (segment_id, sample_index);
        """
    )
    op.execute(
        """
        CREATE INDEX segment_imagery_capture_date_idx
            ON segment_imagery (capture_date);
        """
    )

    op.execute(f"GRANT SELECT, INSERT ON segment_imagery TO {APP_ROLE_NAME};")


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
