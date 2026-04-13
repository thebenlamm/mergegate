-- Reference DDL. Do not modify directly. Use Alembic migrations.
-- Last synced: 2026-04-11 (v3.1 tables removed, MergeGate-only)

-- Owners table (normalized owner identity, prevents sybil attacks)
CREATE TABLE owners (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    handle      VARCHAR(128) UNIQUE NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Agents table (Glicko-2 rating fields, owner FK)
-- Uses UUID7 PKs generated in Python (not gen_random_uuid) for time-ordering
CREATE TABLE agents (
    id                   UUID PRIMARY KEY,
    agent_name           VARCHAR(64) UNIQUE NOT NULL,
    model                VARCHAR(128) NOT NULL,
    framework            VARCHAR(64),
    owner_id             UUID REFERENCES owners(id),
    api_key_hash         VARCHAR(256) NOT NULL,
    key_fingerprint      VARCHAR(32),
    -- Glicko-2 rating fields (replaces single elo INTEGER from DESIGN.md v0.1)
    rating               DOUBLE PRECISION DEFAULT 1500.0,
    rating_deviation     DOUBLE PRECISION DEFAULT 350.0,
    volatility           DOUBLE PRECISION DEFAULT 0.06,
    -- Activity stats
    problems_solved      INTEGER DEFAULT 0,
    total_submissions    INTEGER DEFAULT 0,
    streak               INTEGER DEFAULT 0,
    languages            TEXT[] DEFAULT '{python,javascript}',
    registered_at        TIMESTAMPTZ DEFAULT NOW(),
    last_active          TIMESTAMPTZ,
    is_verified          BOOLEAN DEFAULT FALSE,
    is_banned            BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_agents_owner ON agents (owner_id);
CREATE INDEX idx_agents_model ON agents (model);
CREATE UNIQUE INDEX idx_agents_key_fingerprint ON agents (key_fingerprint) WHERE key_fingerprint IS NOT NULL;

-- Rating history (per-agent rating snapshots for profile charts)
CREATE TABLE rating_history (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id         UUID REFERENCES agents(id),
    rating           DOUBLE PRECISION NOT NULL,
    rating_deviation DOUBLE PRECISION NOT NULL,
    recorded_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_rating_history_agent ON rating_history (agent_id, recorded_at);

-- Leaderboard materialized view with Glicko-2 confidence intervals
-- Shows "1847 +/- 43" style display (rating_lower, rating_upper = rating +/- 2*RD)
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
ORDER BY a.rating DESC;

-- UNIQUE index required for REFRESH MATERIALIZED VIEW CONCURRENTLY
CREATE UNIQUE INDEX leaderboard_agent_id_idx ON leaderboard (id);

-- =========================================================================
-- MergeGate v4.2 -- Delegation benchmark tables
-- Last synced: 2026-04-10 (0016_mergegate_schema)
-- =========================================================================

-- Task definitions with scoring config and solvability flags
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
);

-- Seeded variants with repo snapshots and resolved checks
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
);

CREATE INDEX idx_mg_variants_task ON mg_task_variants (task_id);

-- Agent work sessions linking agents to task variants
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
);

CREATE INDEX idx_mg_sessions_agent ON mg_sessions (agent_id);
CREATE INDEX idx_mg_sessions_variant ON mg_sessions (variant_id);
CREATE INDEX idx_mg_sessions_status ON mg_sessions (status);

-- Pre-work confidence predictions per session
CREATE TABLE mg_predictions (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
    confidence              DOUBLE PRECISION NOT NULL,
    reasoning               TEXT,
    estimated_difficulty    VARCHAR(16),
    expected_approach       TEXT,
    known_risks             JSONB,
    predicted_at            TIMESTAMPTZ DEFAULT NOW()
);

-- Patch or workspace archive submissions
CREATE TABLE mg_submissions (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
    submission_mode         VARCHAR(24) NOT NULL DEFAULT 'patch',
    patch_text              TEXT,
    patch_format            VARCHAR(16) NOT NULL DEFAULT 'git_diff',
    workspace_archive       TEXT,
    submission_notes        TEXT,
    submitted_at            TIMESTAMPTZ DEFAULT NOW()
);

-- Structured proof-of-correctness bundles
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
);

-- Scoring outcomes with failure classification
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
);

-- Post-result self-assessment by agent
CREATE TABLE mg_reflections (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
    was_surprised           BOOLEAN,
    failure_explanation     TEXT,
    root_cause_guess        VARCHAR(32),
    would_change            TEXT,
    updated_confidence      DOUBLE PRECISION,
    reflected_at            TIMESTAMPTZ DEFAULT NOW()
);

-- Append-only event log per session
CREATE TABLE mg_session_events (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES mg_sessions(id),
    event_type              VARCHAR(32) NOT NULL,
    event_data              JSONB,
    occurred_at             TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_mg_events_session ON mg_session_events (session_id, occurred_at);
