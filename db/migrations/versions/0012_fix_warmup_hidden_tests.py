"""Fix warm-up problem hidden_tests key: rename 'output' to 'expected_output'.

Migration 0011 inserted the Echo warm-up with hidden_tests using "output" as
the value key, but executor.py reads test_case["expected_output"]. This
data-fix migration patches any existing rows so production databases match
the corrected 0011 source.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-30
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        text("""
        UPDATE problems
        SET hidden_tests = (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'input', elem->>'input',
                    'expected_output', COALESCE(elem->>'expected_output', elem->>'output')
                )
            )
            FROM jsonb_array_elements(hidden_tests) AS elem
        )
        WHERE is_warmup = TRUE;
        """)
    )


def downgrade() -> None:
    # No-op: renaming back to "output" would break executor.py which expects
    # "expected_output". The forward migration is idempotent and safe to keep.
    pass
