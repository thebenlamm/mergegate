"""Add is_warmup column to problems table and insert Echo warm-up problem.

The is_warmup column marks a problem as a zero-stakes onboarding experience
for new agents. Warm-up submissions do not affect rating, total_submissions,
or problems_solved counts (ONBOARD-02/03).

The Echo problem (print stdin to stdout) is the canonical warm-up: trivially
easy, purely tests the agent's I/O pipeline.

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f4a5b6
Create Date: 2026-03-30
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TABLE first so the column exists before the INSERT references it
    op.execute(
        text("ALTER TABLE problems ADD COLUMN IF NOT EXISTS is_warmup BOOLEAN DEFAULT FALSE;")
    )

    # INSERT Echo warm-up problem only if none exists yet (idempotent)
    op.execute(
        text("""
        INSERT INTO problems (id, class_id, title, difficulty, elo_rating, category,
            description, constraints, examples, hidden_tests, languages, max_attempts,
            is_active, is_warmup)
        SELECT 'mc_prob_' || lpad(nextval('problem_id_seq')::text, 4, '0'),
            NULL, 'Echo', 'easy', 800, ARRAY['warmup'],
            'Read one line from stdin and print it back to stdout unchanged.',
            '{"time_limit_ms": 3000, "memory_limit_mb": 256, "input_size": "1 line"}'::jsonb,
            '[{"input": "hello", "output": "hello", "explanation": "Print the input as-is."}]'::jsonb,
            '[{"input": "hello", "expected_output": "hello"}, {"input": "the quick brown fox", "expected_output": "the quick brown fox"}, {"input": "12345", "expected_output": "12345"}, {"input": " spaces ", "expected_output": " spaces "}, {"input": "\\u3053\\u3093\\u306b\\u3061\\u306f", "expected_output": "\\u3053\\u3093\\u306b\\u3061\\u306f"}]'::jsonb,
            ARRAY['python', 'javascript'], 10, TRUE, TRUE
        WHERE NOT EXISTS (SELECT 1 FROM problems WHERE is_warmup = TRUE);
        """)
    )


def downgrade() -> None:
    op.execute(text("ALTER TABLE problems DROP COLUMN IF EXISTS is_warmup;"))
