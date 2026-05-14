"""Add composite_risk and propagation_uplift columns to segment_scores.

Phase 4 introduces the composite-risk surface and its propagation
uplift. Both are per-(segment_id, scoring_run_id, t) -- matching the
existing hourly-row shape from Phase 2. NOT NULL is the reproducibility
gate: every Phase 4 scoring run writes both columns; pre-Phase 4 rows
live under their own scoring_run_ids and aren't read alongside Phase 4
rows.

Backfill is intentionally NOT required. Mixing Phase 2/3 rows
(composite_risk implicit-zero) with Phase 4 rows (composite_risk real)
would obscure run-over-run comparisons -- callers always query by
scoring_run_id which already segregates phases.

Schema notes:
- ``composite_risk`` is the headline number surfaced in the API and
  on the tile attribute (Phase 4.7). Range is [0, 2] in the current
  weighting; the column does not enforce that range because future
  weight tweaks (recorded in ADR 0006's parameters) can push the
  upper bound up or down.
- ``propagation_uplift`` is the portion contributed by the propagator
  (vs ``local_contribution = composite_risk - propagation_uplift``).
  Surfaced separately so explainability stays first-class.

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
            ADD COLUMN composite_risk DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            ADD COLUMN propagation_uplift DOUBLE PRECISION NOT NULL DEFAULT 0.0;
        """
    )

    # Drop the DEFAULT after the column is in place. Phase 4 scoring
    # runs MUST supply both values explicitly; the DEFAULT existed
    # only to satisfy the NOT NULL constraint on pre-Phase 4 rows
    # that get re-evaluated (none expected in steady state, but the
    # constraint is the reproducibility gate not a backfill mechanism).
    op.execute(
        """
        ALTER TABLE segment_scores
            ALTER COLUMN composite_risk DROP DEFAULT,
            ALTER COLUMN propagation_uplift DROP DEFAULT;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only -- write a new revision.")
