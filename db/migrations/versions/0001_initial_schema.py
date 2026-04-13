"""Initial schema — legacy competitive-programming baseline (historical).

NOTE: Migrations 0001–0015 were written for the v1–v3 competitive-programming
product that was pivoted away from. They create tables (owners, agents,
problem_classes, problems, submissions, calibration_results, rating_history,
leaderboard) that MergeGate (0016) does not use directly, though the `agents`
and `rating_history` tables are still referenced by the hosted API.

They are retained in-place because live deployments rely on the full chain;
squashing would break existing databases. A fresh install still runs the
whole chain via `alembic upgrade head`.

Revision ID: 0001
Revises: None
Create Date: 2026-03-28
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # asyncpg requires one statement per execute() call
    op.execute(
        text("""
        CREATE TABLE owners (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            handle      VARCHAR(128) UNIQUE NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )

    op.execute(
        text("""
        CREATE TABLE agents (
            id                   UUID PRIMARY KEY,
            agent_name           VARCHAR(64) UNIQUE NOT NULL,
            model                VARCHAR(128) NOT NULL,
            framework            VARCHAR(64),
            owner_id             UUID REFERENCES owners(id),
            api_key_hash         VARCHAR(256) NOT NULL,
            rating               DOUBLE PRECISION DEFAULT 1500.0,
            rating_deviation     DOUBLE PRECISION DEFAULT 350.0,
            volatility           DOUBLE PRECISION DEFAULT 0.06,
            problems_solved      INTEGER DEFAULT 0,
            total_submissions    INTEGER DEFAULT 0,
            streak               INTEGER DEFAULT 0,
            languages            TEXT[] DEFAULT '{python,javascript}',
            registered_at        TIMESTAMPTZ DEFAULT NOW(),
            last_active          TIMESTAMPTZ,
            is_verified          BOOLEAN DEFAULT FALSE,
            is_banned            BOOLEAN DEFAULT FALSE
        )
    """)
    )
    op.execute(text("CREATE INDEX idx_agents_owner ON agents (owner_id)"))
    op.execute(text("CREATE INDEX idx_agents_model ON agents (model)"))

    op.execute(
        text("""
        CREATE TABLE problem_classes (
            id                   VARCHAR(32) PRIMARY KEY,
            title                VARCHAR(256) NOT NULL,
            difficulty           VARCHAR(16) NOT NULL,
            category             TEXT[] NOT NULL,
            description_template TEXT NOT NULL,
            parameter_schema     JSONB NOT NULL,
            edge_case_types      TEXT[],
            created_at           TIMESTAMPTZ DEFAULT NOW(),
            created_by           VARCHAR(64),
            is_active            BOOLEAN DEFAULT TRUE
        )
    """)
    )

    op.execute(
        text("""
        CREATE TABLE problems (
            id               VARCHAR(32) PRIMARY KEY,
            class_id         VARCHAR(32) REFERENCES problem_classes(id),
            title            VARCHAR(256) NOT NULL,
            difficulty       VARCHAR(16) NOT NULL,
            elo_rating       INTEGER NOT NULL,
            category         TEXT[] NOT NULL,
            description      TEXT NOT NULL,
            constraints      JSONB NOT NULL,
            examples         JSONB NOT NULL,
            hidden_tests     JSONB NOT NULL,
            solution_code    TEXT,
            witness_hash     VARCHAR(64),
            generated_params JSONB,
            languages        TEXT[] DEFAULT '{python,javascript}',
            max_attempts     INTEGER DEFAULT 3,
            created_at       TIMESTAMPTZ DEFAULT NOW(),
            created_by       VARCHAR(64),
            is_active        BOOLEAN DEFAULT TRUE,
            solve_count      INTEGER DEFAULT 0,
            attempt_count    INTEGER DEFAULT 0
        )
    """)
    )
    op.execute(text("CREATE INDEX idx_problems_class ON problems (class_id)"))
    op.execute(text("CREATE INDEX idx_problems_difficulty ON problems (difficulty)"))
    op.execute(text("CREATE INDEX idx_problems_category ON problems USING GIN (category)"))

    op.execute(
        text("""
        CREATE TABLE submissions (
            id                 UUID PRIMARY KEY,
            agent_id           UUID REFERENCES agents(id),
            problem_id         VARCHAR(32) REFERENCES problems(id),
            language           VARCHAR(16) NOT NULL,
            code               TEXT NOT NULL,
            approach_notes     TEXT,
            status             VARCHAR(16) DEFAULT 'queued',
            verdict            VARCHAR(24),
            tests_passed       INTEGER,
            tests_total        INTEGER,
            runtime_ms         INTEGER,
            memory_mb          NUMERIC(6,1),
            runtime_percentile INTEGER,
            elo_before         DOUBLE PRECISION,
            elo_after          DOUBLE PRECISION,
            first_failing_input TEXT,
            submitted_at       TIMESTAMPTZ DEFAULT NOW(),
            completed_at       TIMESTAMPTZ
        )
    """)
    )
    op.execute(text("CREATE INDEX idx_submissions_agent ON submissions (agent_id)"))
    op.execute(text("CREATE INDEX idx_submissions_problem ON submissions (problem_id)"))
    op.execute(
        text(
            "CREATE INDEX idx_submissions_rate_limit ON submissions (agent_id, problem_id, submitted_at)"
        )
    )
    op.execute(text("CREATE INDEX idx_submissions_status ON submissions (status)"))
    op.execute(
        text("CREATE INDEX idx_submissions_agent_problem ON submissions (agent_id, problem_id)")
    )

    op.execute(
        text("""
        CREATE TABLE calibration_results (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            problem_id   VARCHAR(32) REFERENCES problems(id),
            model_id     VARCHAR(128) NOT NULL,
            passed       BOOLEAN NOT NULL,
            runtime_ms   INTEGER,
            attempted_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )
    op.execute(text("CREATE INDEX idx_calibration_problem ON calibration_results (problem_id)"))

    op.execute(
        text("""
        CREATE TABLE rating_history (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_id         UUID REFERENCES agents(id),
            rating           DOUBLE PRECISION NOT NULL,
            rating_deviation DOUBLE PRECISION NOT NULL,
            recorded_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    )
    op.execute(
        text("CREATE INDEX idx_rating_history_agent ON rating_history (agent_id, recorded_at)")
    )

    op.execute(
        text("""
        CREATE MATERIALIZED VIEW leaderboard AS
        SELECT
            a.id,
            a.agent_name,
            a.model,
            a.rating,
            a.rating_deviation,
            a.volatility,
            a.problems_solved,
            a.streak,
            a.rating - 2 * a.rating_deviation AS rating_lower,
            a.rating + 2 * a.rating_deviation AS rating_upper,
            RANK() OVER (ORDER BY a.rating DESC) AS global_rank,
            RANK() OVER (PARTITION BY a.model ORDER BY a.rating DESC) AS model_rank
        FROM agents a
        WHERE a.is_banned = FALSE
        ORDER BY a.rating DESC
    """)
    )
    op.execute(text("CREATE UNIQUE INDEX leaderboard_agent_id_idx ON leaderboard (id)"))


def downgrade() -> None:
    op.execute(text("DROP MATERIALIZED VIEW IF EXISTS leaderboard"))
    op.execute(text("DROP TABLE IF EXISTS rating_history"))
    op.execute(text("DROP TABLE IF EXISTS calibration_results"))
    op.execute(text("DROP TABLE IF EXISTS submissions"))
    op.execute(text("DROP TABLE IF EXISTS problems"))
    op.execute(text("DROP TABLE IF EXISTS problem_classes"))
    op.execute(text("DROP TABLE IF EXISTS agents"))
    op.execute(text("DROP TABLE IF EXISTS owners"))
