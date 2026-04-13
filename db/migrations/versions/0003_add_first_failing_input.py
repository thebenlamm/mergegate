"""Add first_failing_input column to submissions

For existing deployments that already ran 0001 + 0002, this migration adds
the missing first_failing_input column that the submission pipeline reads/writes.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-03-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE submissions ADD COLUMN IF NOT EXISTS first_failing_input TEXT;")


def downgrade() -> None:
    op.execute("ALTER TABLE submissions DROP COLUMN IF EXISTS first_failing_input;")
