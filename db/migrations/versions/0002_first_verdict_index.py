"""Add first-verdict partial unique index for rating guard

Adds a unique partial index on (agent_id, problem_id) WHERE elo_after IS NOT NULL.
This prevents duplicate rating updates at the DB level if application logic
has a race condition where two concurrent requests both pass the first-verdict
check before either commits (RANK-03 DB-level guard).

The index only covers rows that have a rating update (elo_after IS NOT NULL),
so regular queued/running submissions are not affected.

Revision ID: a2b3c4d5e6f7
Revises: 0001
Create Date: 2026-03-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX idx_submissions_first_verdict
            ON submissions (agent_id, problem_id)
            WHERE elo_after IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_submissions_first_verdict;")
