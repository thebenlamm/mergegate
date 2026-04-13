"""Add seed column to problems, backfill active rows, replace witness_hash unique index.

The witness_hash dedup from migration 0013 is too aggressive -- all seeds within
a class share identical witness code, so the unique index collapsed 5 intended
variants into 1.  This migration adds a seed column and switches the unique
constraint to (class_id, seed) so each seed-variant is correctly preserved.

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-03-30
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Add seed column (nullable — existing rows get NULL initially)
    op.execute(text("ALTER TABLE problems ADD COLUMN seed INTEGER"))

    # Step 2: Backfill active problems with sequential seeds per class.
    # Most classes have 1 active row (seed=1), but classes with per-seed
    # witness variation (e.g. mc_class_0020) kept multiple active rows
    # through migration 0013's witness_hash dedup.
    op.execute(
        text("""
        UPDATE problems p
        SET seed = numbered.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY class_id ORDER BY id) AS rn
            FROM problems
            WHERE is_active = TRUE AND seed IS NULL
        ) numbered
        WHERE p.id = numbered.id
        """)
    )

    # Step 3: Drop the witness_hash unique index (wrong dedup key)
    op.execute(text("DROP INDEX IF EXISTS uq_problems_witness_hash_active"))

    # Step 4: Create correct unique index on (class_id, seed) for active problems
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_problems_class_seed_active "
            "ON problems (class_id, seed) WHERE is_active = TRUE AND seed IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS uq_problems_class_seed_active"))
    op.execute(
        text(
            "CREATE UNIQUE INDEX uq_problems_witness_hash_active "
            "ON problems (witness_hash) WHERE is_active = TRUE"
        )
    )
    op.execute(text("ALTER TABLE problems DROP COLUMN seed"))
