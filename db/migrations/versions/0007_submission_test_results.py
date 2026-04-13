"""Create submission_test_results table for per-test-case analytics

Stores individual test case results for each submission, enabling:
- Per-test verdict, runtime, and memory tracking
- Actual output storage for failing tests (calibration analytics)
- Downstream difficulty calibration and problem quality analysis

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-03-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS submission_test_results (
            id            UUID PRIMARY KEY,
            submission_id UUID NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
            test_index    INTEGER NOT NULL,
            verdict       VARCHAR(24) NOT NULL,
            runtime_ms    INTEGER,
            memory_mb     NUMERIC(6,1),
            actual_output TEXT
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_str_submission ON submission_test_results (submission_id);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS submission_test_results;")
