"""Per-sub-score `is_stub_*` flags on segment_scores.

Phase 1 carried a single top-level `risk_stub: bool` in API responses.
Phase 2 starts filling in real sub-scores one at a time (glare first),
so the flag has to live per-sub-score in storage as well as in the API.

Each row written in Phase 2 sets `is_stub_glare = false` and the other
three flags `= true`. Phase 3 flips `is_stub_lane_marking` (and likely
`is_stub_junction_complexity` if the junction-topology scorer ships
then); Phase 4 flips `is_stub_historical` and removes the composite's
stubbed-ness.

`NOT NULL DEFAULT true` is the safe-by-default: any row written without
explicit flags is treated as a stub by consumers.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE segment_scores
            ADD COLUMN is_stub_lane_marking        boolean NOT NULL DEFAULT true,
            ADD COLUMN is_stub_glare               boolean NOT NULL DEFAULT true,
            ADD COLUMN is_stub_junction_complexity boolean NOT NULL DEFAULT true,
            ADD COLUMN is_stub_historical          boolean NOT NULL DEFAULT true;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only — write a new revision.")
