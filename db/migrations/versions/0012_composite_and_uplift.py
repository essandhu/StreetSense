"""Add propagation_uplift column to segment_scores.

Phase 4 introduces the propagator's contribution to composite risk as
a first-class column. ``composite_risk`` itself already exists from
migration 0001 (NOT NULL, no default); this migration adds the
explainability-companion column that splits the composite into
``local_contribution + propagation_uplift``. NOT NULL is the
reproducibility gate: every Phase 4 scoring run writes the column;
pre-Phase 4 rows live under their own scoring_run_ids and aren't read
alongside Phase 4 rows.

Backfill is intentionally NOT required. Mixing Phase 2/3 rows
(propagation_uplift implicit-zero) with Phase 4 rows (real uplift)
would obscure run-over-run comparisons -- callers always query by
scoring_run_id which already segregates phases.

Schema notes:
- ``propagation_uplift`` is the portion contributed by the propagator
  (vs ``local_contribution = composite_risk - propagation_uplift``).
  Surfaced separately so explainability stays first-class. The column
  does not enforce a range because future weight/decay tweaks
  (recorded in ADR 0006's parameters) can push the upper bound up or
  down.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE segment_scores
            ADD COLUMN propagation_uplift DOUBLE PRECISION NOT NULL DEFAULT 0.0;
        """
    )

    # Drop the DEFAULT after the column is in place. Phase 4 scoring
    # runs MUST supply the value explicitly; the DEFAULT existed only
    # to satisfy the NOT NULL constraint on the existing rows the ADD
    # COLUMN touches.
    op.execute(
        """
        ALTER TABLE segment_scores
            ALTER COLUMN propagation_uplift DROP DEFAULT;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only -- write a new revision.")
