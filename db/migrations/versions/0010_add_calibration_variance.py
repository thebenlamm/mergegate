"""Add calibration_variance and class_variance_summary tables for intra-model variance pipeline.

calibration_variance stores per-problem per-variant pass/fail outcomes from
scaffolding variant simulation.

class_variance_summary stores per-class aggregate variance metrics for
identifying classes that cannot distinguish scaffolding quality (zero-variance
classes are retirement candidates).

Revision ID: c1d2e3f4a5b6
Revises: b9c0d1e2f3a4
Create Date: 2026-03-29
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        text("""
        CREATE TABLE calibration_variance (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            problem_id      VARCHAR(32) REFERENCES problems(id),
            variant_id      VARCHAR(64) NOT NULL,
            model_id        VARCHAR(128) NOT NULL,
            passed          BOOLEAN NOT NULL,
            calibrated_at   TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (problem_id, variant_id)
        )
    """)
    )
    op.execute(
        text("CREATE INDEX idx_calibration_variance_problem ON calibration_variance (problem_id)")
    )

    op.execute(
        text("""
        CREATE TABLE class_variance_summary (
            class_id            VARCHAR(32) PRIMARY KEY REFERENCES problem_classes(id),
            model_id            VARCHAR(128) NOT NULL,
            variant_count       INTEGER NOT NULL,
            problems_tested     INTEGER NOT NULL,
            avg_variance        DOUBLE PRECISION NOT NULL,
            max_variance        DOUBLE PRECISION NOT NULL,
            zero_variance_flag  BOOLEAN NOT NULL DEFAULT FALSE,
            last_updated        TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS class_variance_summary"))
    op.execute(text("DROP TABLE IF EXISTS calibration_variance"))
