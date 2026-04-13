"""Add key_fingerprint column to agents for O(1) auth lookup

Adds a VARCHAR(32) column for storing the first 16 hex chars of SHA-256(raw_api_key).
Creates a partial unique index on non-NULL values so existing rows (NULL) don't conflict.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-03-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE agents ADD COLUMN IF NOT EXISTS key_fingerprint VARCHAR(32);")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_key_fingerprint "
        "ON agents (key_fingerprint) WHERE key_fingerprint IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agents_key_fingerprint;")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS key_fingerprint;")
