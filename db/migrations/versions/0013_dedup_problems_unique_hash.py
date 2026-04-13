"""Deduplicate active problems by witness_hash and add partial unique index.

For each witness_hash with multiple active rows, keep the one with the
lowest problem_id (lexicographic MIN on mc_prob_NNNN) and deactivate the
rest. Then add a partial unique index so future duplicates are prevented
at the DB level.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-30
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Deactivate duplicate active problems, keeping the lowest ID
    op.execute(
        text("""
        UPDATE problems SET is_active = FALSE
        WHERE id IN (
            SELECT p.id FROM problems p
            INNER JOIN (
                SELECT witness_hash, MIN(id) AS keep_id
                FROM problems
                WHERE witness_hash IS NOT NULL AND is_active = TRUE
                GROUP BY witness_hash
                HAVING COUNT(*) > 1
            ) dups ON p.witness_hash = dups.witness_hash AND p.id != dups.keep_id
            WHERE p.is_active = TRUE
        )
        """)
    )

    # Step 2: Add partial unique index on witness_hash for active problems
    op.execute(
        text("""
        CREATE UNIQUE INDEX uq_problems_witness_hash_active
        ON problems (witness_hash) WHERE is_active = TRUE
        """)
    )


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS uq_problems_witness_hash_active"))
