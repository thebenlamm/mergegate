"""Add problem_id_seq sequence for atomic problem ID allocation

Replaces the race-prone SELECT MAX(id) pattern in _next_problem_id()
with a PostgreSQL sequence. The sequence is initialized to the current
maximum problem number so new IDs continue the existing series.
"""

from typing import Union

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE IF NOT EXISTS problem_id_seq;")
    op.execute("""
        SELECT setval('problem_id_seq',
            GREATEST(
                COALESCE(
                    (SELECT MAX(CAST(SUBSTRING(id FROM 9) AS INTEGER)) FROM problems),
                    0
                ),
                1
            )
        );
    """)


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS problem_id_seq;")
