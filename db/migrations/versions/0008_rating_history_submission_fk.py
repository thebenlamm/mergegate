"""Add submission_id FK to rating_history for traceability

Links each rating history row to the submission that caused the rating change.
Nullable so existing rows (which pre-date this migration) remain valid.
Enables analytics queries like "which submission caused this rating jump".

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-03-29
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rating_history "
        "ADD COLUMN IF NOT EXISTS submission_id UUID REFERENCES submissions(id);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rating_history DROP COLUMN IF EXISTS submission_id;")
