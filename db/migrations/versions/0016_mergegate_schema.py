"""MergeGate schema — delegation benchmark tables (mg_* prefix)

Creates all MergeGate tables:
- mg_tasks: task definitions with scoring config and solvability flags
- mg_task_variants: seeded variants with repo snapshots and resolved checks
- mg_sessions: agent work sessions linking agents to variants
- mg_predictions: pre-work confidence predictions per session
- mg_submissions: patch or workspace archive submissions
- mg_proof_bundles: structured proof-of-correctness bundles
- mg_results: scoring outcomes with failure classification
- mg_reflections: post-result self-assessment by agent
- mg_session_events: append-only event log per session

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-10
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        text("""
        CREATE TABLE mg_tasks (
            id              VARCHAR(32) PRIMARY KEY,
            task_family     VARCHAR(32) NOT NULL DEFAULT 'mergegate',
            title           VARCHAR(256) NOT NULL,
            description     TEXT NOT NULL,
            difficulty      VARCHAR(16) NOT NULL,
            category        TEXT[] NOT NULL,
            repo_source     TEXT NOT NULL,
            base_checks     JSONB NOT NULL,
            scoring_config  JSONB NOT NULL,
            is_solvable     BOOLEAN DEFAULT TRUE,
            unsolvable_reason TEXT,
            variant_schema  JSONB,
            max_duration_s  INTEGER DEFAULT 600,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            is_active       BOOLEAN DEFAULT TRUE
        )
    """)
    )

    op.execute(
        text("""
        CREATE TABLE mg_task_variants (
            id                  VARCHAR(48) PRIMARY KEY,
            task_id             VARCHAR(32) NOT NULL REFERENCES mg_tasks(id),
            variant_params      JSONB NOT NULL,
            repo_snapshot       TEXT NOT NULL,
            repo_snapshot_hash  VARCHAR(64) NOT NULL,
            resolved_checks     JSONB NOT NULL,
            spec_text           TEXT NOT NULL,
            spec_hash           VARCHAR(64) NOT NULL,
            seed                INTEGER NOT NULL,
            generator_version   VARCHAR(32) NOT NULL,
            created_at          TIMESTAMPTZ DEFAULT NOW(),
            is_active           BOOLEAN DEFAULT TRUE,
            UNIQUE (task_id, seed)
        )
    """)
    )
    op.execute(text("CREATE INDEX idx_mg_variants_task ON mg_task_variants (task_id)"))

    op.execute(
        text("""
        CREATE TABLE mg_sessions (
            id                      UUID PRIMARY KEY,
            agent_id                UUID NOT NULL REFERENCES agents(id),
            variant_id              VARCHAR(48) NOT NULL REFERENCES mg_task_variants(id),
            status                  VARCHAR(16) DEFAULT 'pending',
            sandbox_ref             TEXT,
            submission_deadline_at  TIMESTAMPTZ,
            started_at              TIMESTAMPTZ,
            submitted_at            TIMESTAMPTZ,
            completed_at            TIMESTAMPTZ,
            duration_s              INTEGER,
            created_at              TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )
    op.execute(text("CREATE INDEX idx_mg_sessions_agent ON mg_sessions (agent_id)"))
    op.execute(text("CREATE INDEX idx_mg_sessions_variant ON mg_sessions (variant_id)"))
    op.execute(text("CREATE INDEX idx_mg_sessions_status ON mg_sessions (status)"))

    op.execute(
        text("""
        CREATE TABLE mg_predictions (
            id                      UUID PRIMARY KEY,
            session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
            confidence              DOUBLE PRECISION NOT NULL,
            reasoning               TEXT,
            estimated_difficulty    VARCHAR(16),
            expected_approach       TEXT,
            known_risks             JSONB,
            predicted_at            TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )

    op.execute(
        text("""
        CREATE TABLE mg_submissions (
            id                      UUID PRIMARY KEY,
            session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
            submission_mode         VARCHAR(24) NOT NULL DEFAULT 'patch',
            patch_text              TEXT,
            patch_format            VARCHAR(16) NOT NULL DEFAULT 'git_diff',
            workspace_archive       TEXT,
            submission_notes        TEXT,
            submitted_at            TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )

    op.execute(
        text("""
        CREATE TABLE mg_proof_bundles (
            id                      UUID PRIMARY KEY,
            session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
            schema_version          VARCHAR(8) NOT NULL DEFAULT '1.0',
            tests_run               JSONB,
            files_changed           JSONB,
            assumptions_json        JSONB,
            not_verified_json       JSONB,
            residual_risks_json     JSONB,
            correctness_argument    TEXT,
            rollback_plan           TEXT,
            final_confidence        DOUBLE PRECISION,
            raw_bundle              JSONB,
            submitted_at            TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )

    op.execute(
        text("""
        CREATE TABLE mg_results (
            id                      UUID PRIMARY KEY,
            session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
            scoring_version         VARCHAR(32) NOT NULL,
            task_success            BOOLEAN NOT NULL,
            hidden_tests_passed     INTEGER,
            hidden_tests_total      INTEGER,
            regressions_found       INTEGER DEFAULT 0,
            mergeable               BOOLEAN,
            approval_proxy_score    DOUBLE PRECISION,
            proof_completeness      DOUBLE PRECISION,
            review_cost_proxy       DOUBLE PRECISION,
            review_cost_confidence  VARCHAR(8),
            confidence_declared     DOUBLE PRECISION,
            confidence_gap          DOUBLE PRECISION,
            failure_class           VARCHAR(32),
            failure_severity        VARCHAR(8),
            failure_detail          TEXT,
            failure_signature       VARCHAR(64),
            is_silent_failure       BOOLEAN DEFAULT FALSE,
            correctly_refused       BOOLEAN,
            refusal_quality         DOUBLE PRECISION,
            quality_floor_passed    BOOLEAN,
            safety_floor_passed     BOOLEAN,
            scored_at               TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )

    op.execute(
        text("""
        CREATE TABLE mg_reflections (
            id                      UUID PRIMARY KEY,
            session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
            was_surprised           BOOLEAN,
            failure_explanation     TEXT,
            root_cause_guess        VARCHAR(32),
            would_change            TEXT,
            updated_confidence      DOUBLE PRECISION,
            reflected_at            TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )

    op.execute(
        text("""
        CREATE TABLE mg_session_events (
            id                      UUID PRIMARY KEY,
            session_id              UUID NOT NULL REFERENCES mg_sessions(id),
            event_type              VARCHAR(32) NOT NULL,
            event_data              JSONB,
            occurred_at             TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )
    op.execute(
        text("CREATE INDEX idx_mg_events_session ON mg_session_events (session_id, occurred_at)")
    )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS mg_session_events"))
    op.execute(text("DROP TABLE IF EXISTS mg_reflections"))
    op.execute(text("DROP TABLE IF EXISTS mg_results"))
    op.execute(text("DROP TABLE IF EXISTS mg_proof_bundles"))
    op.execute(text("DROP TABLE IF EXISTS mg_submissions"))
    op.execute(text("DROP TABLE IF EXISTS mg_predictions"))
    op.execute(text("DROP TABLE IF EXISTS mg_sessions"))
    op.execute(text("DROP TABLE IF EXISTS mg_task_variants"))
    op.execute(text("DROP TABLE IF EXISTS mg_tasks"))
