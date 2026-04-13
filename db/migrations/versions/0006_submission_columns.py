"""Add code_hash, first_failing_actual_output, and is_rated columns to submissions

Adds three columns to support per-test analytics and plagiarism detection:
- code_hash: SHA-256 of submitted code for deduplication and anti-cheat
- first_failing_actual_output: actual stdout on first failing test case
- is_rated: whether this submission counted toward a rating update

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-03-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS code_hash VARCHAR(64);")
    op.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS first_failing_actual_output TEXT;")
    op.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS is_rated BOOLEAN DEFAULT TRUE;")


def downgrade() -> None:
    op.execute("ALTER TABLE submissions DROP COLUMN IF EXISTS is_rated;")
    op.execute("ALTER TABLE submissions DROP COLUMN IF EXISTS first_failing_actual_output;")
    op.execute("ALTER TABLE submissions DROP COLUMN IF EXISTS code_hash;")
