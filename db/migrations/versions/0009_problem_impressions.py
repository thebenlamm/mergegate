"""Create problem_impressions table to track problem serving events

Records when a problem is served to an agent (via /next or /browse),
enabling impression-to-attempt funnel analysis and anti-gaming detection
(e.g., an agent repeatedly fetching /next without attempting).

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-03-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS problem_impressions (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id   UUID NOT NULL REFERENCES agents(id),
            problem_id VARCHAR(32) NOT NULL REFERENCES problems(id),
            source     VARCHAR(16) NOT NULL,
            served_at  TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_impressions_agent_problem "
        "ON problem_impressions (agent_id, problem_id, served_at);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS problem_impressions;")
