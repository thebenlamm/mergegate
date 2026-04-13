"""Add rating_reason to submissions, judge_notes to problems.

rating_reason: nullable VARCHAR(32) on submissions — explains why a submission
was or was not rated. NULL means rated normally. Values: "warmup",
"no_next_impression", "not_first_verdict", "compilation_error".

judge_notes: nullable TEXT on problems — used by generation pipeline to store
spec/judge consistency notes (GATE-04, wired in Plan 02).

Backfill logic:
  - is_rated=FALSE and rating_reason IS NULL → "no_next_impression"
  - is_rated=TRUE and elo_after IS NULL and verdict IS NOT NULL → "not_first_verdict"
  - warm-up problems with elo_after IS NULL → "warmup"

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-30
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Add rating_reason column to submissions
    op.execute(text("ALTER TABLE submissions ADD COLUMN rating_reason VARCHAR(32)"))

    # Step 2: Add judge_notes column to problems
    op.execute(text("ALTER TABLE problems ADD COLUMN judge_notes TEXT"))

    # Step 3: Backfill rating_reason for existing submissions

    # Warm-up submissions that were not rated
    op.execute(
        text("""
        UPDATE submissions s
        SET rating_reason = 'warmup'
        FROM problems p
        WHERE s.problem_id = p.id
          AND p.is_warmup = TRUE
          AND s.elo_after IS NULL
          AND s.rating_reason IS NULL
        """)
    )

    # Unrated submissions (no /next impression)
    op.execute(
        text("""
        UPDATE submissions
        SET rating_reason = 'no_next_impression'
        WHERE is_rated = FALSE
          AND rating_reason IS NULL
        """)
    )

    # Rated but not first verdict (elo_after is NULL despite having a verdict)
    op.execute(
        text("""
        UPDATE submissions
        SET rating_reason = 'not_first_verdict'
        WHERE is_rated = TRUE
          AND elo_after IS NULL
          AND verdict IS NOT NULL
          AND rating_reason IS NULL
        """)
    )


def downgrade() -> None:
    op.execute(text("ALTER TABLE submissions DROP COLUMN rating_reason"))
    op.execute(text("ALTER TABLE problems DROP COLUMN judge_notes"))
