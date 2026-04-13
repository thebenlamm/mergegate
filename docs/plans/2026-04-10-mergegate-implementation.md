# MergeGate Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build MergeGate — a delegation benchmark for coding agents that measures mergeability, review cost, and calibration.

**Architecture:** Parallel product alongside v3.1. Separate routes (`/api/v1/mergegate/*`), separate tables (`mg_*`), shared auth/agents/logging/pool. Submit-only session model — agent downloads repo tarball, works locally, submits patch + proof bundle via API, gets scored. No live shell access in v1.

**Tech Stack:** FastAPI, asyncpg (raw SQL), Alembic, Docker, Pydantic v2, pytest + httpx AsyncClient

**Design Doc:** `docs/plans/2026-04-10-mergegate-design.md`

---

## Phase 1: Data Model + Models (Tasks 1-3)

### Task 1: Alembic Migration for MergeGate Tables

**Files:**
- Create: `db/migrations/versions/0016_mergegate_schema.py`
- Modify: `db/schema.sql` (add MergeGate tables to reference DDL)

**Step 1: Generate the migration file**

Run:
```bash
alembic revision -m "mergegate_schema"
```

Rename the output to `0016_mergegate_schema.py`.

**Step 2: Write the migration**

```python
"""MergeGate schema — parallel product tables for delegation benchmark.

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
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

    CREATE TABLE mg_session_events (
        id                      UUID PRIMARY KEY,
        session_id              UUID NOT NULL REFERENCES mg_sessions(id),
        event_type              VARCHAR(32) NOT NULL,
        event_data              JSONB,
        occurred_at             TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_mg_events_session ON mg_session_events (session_id, occurred_at);
    """)


def downgrade() -> None:
    op.execute("""
    DROP TABLE IF EXISTS mg_session_events;
    DROP TABLE IF EXISTS mg_reflections;
    DROP TABLE IF EXISTS mg_results;
    DROP TABLE IF EXISTS mg_proof_bundles;
    DROP TABLE IF EXISTS mg_submissions;
    DROP TABLE IF EXISTS mg_predictions;
    DROP TABLE IF EXISTS mg_sessions;
    DROP TABLE IF EXISTS mg_task_variants;
    DROP TABLE IF EXISTS mg_tasks;
    """)
```

**Step 3: Update reference DDL**

Append the MergeGate tables to `db/schema.sql` with a `-- MergeGate v4.2` section header. Copy the CREATE statements from the migration.

**Step 4: Run migration against test DB**

```bash
DATABASE_URL=postgresql://mergegate:mergegate@localhost:5433/mergegate_test alembic upgrade head
```

Expected: migration applies cleanly, no errors.

**Step 5: Commit**

```bash
git add db/migrations/versions/0016_mergegate_schema.py db/schema.sql
git commit -m "feat(db): add MergeGate schema (mg_* tables)"
```

---

### Task 2: Pydantic Models for MergeGate

**Files:**
- Create: `api/models/mergegate.py`
- Test: `tests/test_mergegate_models.py`

**Step 1: Write failing tests**

```python
# tests/test_mergegate_models.py
"""Tests for MergeGate Pydantic models."""

import pytest
from pydantic import ValidationError

from api.models.mergegate import (
    CreateSessionRequest,
    PredictionPayload,
    SubmitRequest,
    ProofBundlePayload,
    SessionCreatedResponse,
    SessionResultResponse,
    ReflectionRequest,
    TaskSummary,
)


class TestPredictionPayload:
    def test_valid_prediction(self):
        p = PredictionPayload(
            confidence=0.85,
            reasoning="Standard cache bug",
            estimated_difficulty="medium",
            expected_approach="Fix TTL check",
            known_risks=["concurrency"],
        )
        assert p.confidence == 0.85
        assert p.known_risks == ["concurrency"]

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            PredictionPayload(confidence=1.5)
        with pytest.raises(ValidationError):
            PredictionPayload(confidence=-0.1)


class TestCreateSessionRequest:
    def test_with_task_id(self):
        r = CreateSessionRequest(task_id="mg_task_0001")
        assert r.task_id == "mg_task_0001"
        assert r.use_next is False

    def test_use_next(self):
        r = CreateSessionRequest(use_next=True)
        assert r.use_next is True
        assert r.task_id is None


class TestProofBundlePayload:
    def test_valid_patch_bundle(self):
        b = ProofBundlePayload(
            schema_version="1.0",
            tests_run=[{"name": "test_x", "passed": True}],
            files_changed=[{"path": "a.py", "change_type": "modified", "summary": "fix"}],
            assumptions=["key is string"],
            correctness_argument="The bug was a strict inequality...",
            rollback_plan="revert abc123",
            residual_risks=["edge case"],
        )
        assert b.schema_version == "1.0"

    def test_refusal_bundle(self):
        b = ProofBundlePayload(
            schema_version="1.0",
            submission_mode="refusal",
            correctness_argument="Cannot be solved because...",
        )
        assert b.submission_mode == "refusal"


class TestSubmitRequest:
    def test_patch_submission(self):
        r = SubmitRequest(
            submission_mode="patch",
            patch_text="diff --git a/foo.py...",
            proof_bundle=ProofBundlePayload(
                schema_version="1.0",
                correctness_argument="works",
            ),
        )
        assert r.submission_mode == "patch"

    def test_refusal_no_patch(self):
        r = SubmitRequest(
            submission_mode="refusal",
            proof_bundle=ProofBundlePayload(
                schema_version="1.0",
                submission_mode="refusal",
                correctness_argument="unsolvable",
            ),
        )
        assert r.patch_text is None

    def test_invalid_mode(self):
        with pytest.raises(ValidationError):
            SubmitRequest(
                submission_mode="invalid",
                proof_bundle=ProofBundlePayload(schema_version="1.0", correctness_argument="x"),
            )
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_mergegate_models.py -v
```

Expected: ImportError — `api.models.mergegate` does not exist.

**Step 3: Write the models**

```python
# api/models/mergegate.py
"""Pydantic models for MergeGate endpoints.

Request/response models for the delegation benchmark API.
"""

from pydantic import BaseModel, Field, field_validator

# Valid submission modes
_SUBMISSION_MODES = {"patch", "refusal", "clarification_request"}

# Valid difficulties
_DIFFICULTIES = {"easy", "medium", "hard", "nightmare"}


class PredictionPayload(BaseModel):
    """Pre-task confidence prediction."""

    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str | None = None
    estimated_difficulty: str | None = None
    expected_approach: str | None = None
    known_risks: list[str] | None = None

    @field_validator("estimated_difficulty")
    @classmethod
    def valid_difficulty(cls, v: str | None) -> str | None:
        if v is not None and v not in _DIFFICULTIES:
            raise ValueError(f"Invalid difficulty: {v}")
        return v


class CreateSessionRequest(BaseModel):
    """Request body for POST /api/v1/mergegate/sessions."""

    task_id: str | None = None
    use_next: bool = False
    prediction: PredictionPayload | None = None


class ProofBundlePayload(BaseModel):
    """Proof bundle submitted with the work product."""

    schema_version: str = "1.0"
    submission_mode: str = "patch"
    tests_run: list[dict] | None = None
    files_changed: list[dict] | None = None
    assumptions: list[str] | None = None
    not_verified: list[str] | None = None
    correctness_argument: str | None = None
    rollback_plan: str | None = None
    residual_risks: list[str] | None = None
    final_confidence: float | None = Field(None, ge=0.0, le=1.0)


class SubmitRequest(BaseModel):
    """Request body for POST /api/v1/mergegate/sessions/{id}/submit."""

    submission_mode: str = "patch"
    patch_text: str | None = None
    patch_format: str = "git_diff"
    submission_notes: str | None = None
    proof_bundle: ProofBundlePayload

    @field_validator("submission_mode")
    @classmethod
    def valid_mode(cls, v: str) -> str:
        if v not in _SUBMISSION_MODES:
            raise ValueError(f"Invalid submission_mode: {v}. Valid: {_SUBMISSION_MODES}")
        return v


class ReflectionRequest(BaseModel):
    """Request body for POST /api/v1/mergegate/sessions/{id}/reflect."""

    was_surprised: bool | None = None
    failure_explanation: str | None = None
    root_cause_guess: str | None = None
    would_change: str | None = None
    updated_confidence: float | None = Field(None, ge=0.0, le=1.0)


class SessionCreatedResponse(BaseModel):
    """Response from POST /api/v1/mergegate/sessions."""

    session_id: str
    variant_id: str
    spec_text: str
    repo_download_url: str
    deadline: str
    submission_contract: dict


class SessionSubmittedResponse(BaseModel):
    """Response from POST /api/v1/mergegate/sessions/{id}/submit."""

    session_id: str
    status: str
    scoring_eta_s: int = 30


class SessionResultResponse(BaseModel):
    """Response from GET /api/v1/mergegate/sessions/{id}/result."""

    session_id: str
    status: str
    task_success: bool | None = None
    mergeable: bool | None = None
    hidden_tests_passed: int | None = None
    hidden_tests_total: int | None = None
    regressions_found: int | None = None
    approval_proxy_score: float | None = None
    proof_completeness: float | None = None
    review_cost_proxy: float | None = None
    confidence_declared: float | None = None
    confidence_gap: float | None = None
    failure_class: str | None = None
    failure_severity: str | None = None
    failure_detail: str | None = None
    failure_signature: str | None = None
    is_silent_failure: bool | None = None
    correctly_refused: bool | None = None
    refusal_quality: float | None = None
    quality_floor_passed: bool | None = None
    safety_floor_passed: bool | None = None
    scored_at: str | None = None


class TaskSummary(BaseModel):
    """Summary for task listing."""

    id: str
    title: str
    difficulty: str
    category: list[str]
    is_solvable: bool
    max_duration_s: int


class RecentSessionItem(BaseModel):
    """Summary row for recent sessions listing."""

    session_id: str
    variant_id: str
    status: str
    task_success: bool | None = None
    mergeable: bool | None = None
    proof_completeness: float | None = None
    created_at: str
```

**Step 4: Run tests**

```bash
python3 -m pytest tests/test_mergegate_models.py -v
```

Expected: all pass.

**Step 5: Commit**

```bash
git add api/models/mergegate.py tests/test_mergegate_models.py
git commit -m "feat(api): add MergeGate Pydantic models"
```

---

### Task 3: Proof Bundle Completeness Scorer

**Files:**
- Create: `api/services/proof_scoring.py`
- Test: `tests/test_proof_scoring.py`

**Step 1: Write failing tests**

```python
# tests/test_proof_scoring.py
"""Tests for proof bundle completeness scoring."""

from api.services.proof_scoring import score_proof_completeness


class TestProofCompleteness:
    def test_perfect_bundle(self):
        bundle = {
            "tests_run": [{"name": "test_x", "passed": True}],
            "files_changed": [{"path": "a.py"}],
            "assumptions": ["key is string"],
            "correctness_argument": "The bug was a strict inequality that should have been...",
            "rollback_plan": "revert commit abc123",
            "residual_risks": ["concurrent access"],
            "not_verified": ["load testing"],
        }
        score = score_proof_completeness(bundle)
        assert score == 1.0

    def test_empty_bundle(self):
        score = score_proof_completeness({})
        assert score == 0.0

    def test_partial_bundle(self):
        bundle = {
            "tests_run": [{"name": "test_x", "passed": True}],
            "correctness_argument": "The bug was a strict inequality that should have been...",
        }
        score = score_proof_completeness(bundle)
        # tests_run (0.20) + correctness_argument (0.20) = 0.40
        assert abs(score - 0.40) < 0.01

    def test_short_correctness_argument_not_counted(self):
        bundle = {
            "correctness_argument": "works",  # < 50 chars
        }
        score = score_proof_completeness(bundle)
        assert score == 0.0

    def test_empty_lists_not_counted(self):
        bundle = {
            "tests_run": [],
            "assumptions": [],
        }
        score = score_proof_completeness(bundle)
        assert score == 0.0
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_proof_scoring.py -v
```

Expected: ImportError.

**Step 3: Write implementation**

```python
# api/services/proof_scoring.py
"""Proof bundle completeness scoring.

v1 completeness is a structural proxy, not a semantic quality score.
It measures whether expected fields are present and non-trivial.
Its correlation with actual review cost is an empirical question
validated via human review sampling.
"""

# Weights for each proof bundle field
_WEIGHTS = {
    "tests_run": 0.20,
    "files_changed": 0.15,
    "assumptions": 0.15,
    "correctness_argument": 0.20,
    "rollback_plan": 0.10,
    "residual_risks": 0.10,
    "not_verified": 0.10,
}

_CORRECTNESS_MIN_LENGTH = 50


def _is_present(bundle: dict, field: str) -> bool:
    """Check if a field is present and non-trivially filled."""
    value = bundle.get(field)
    if value is None:
        return False
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, str):
        if field == "correctness_argument":
            return len(value.strip()) >= _CORRECTNESS_MIN_LENGTH
        return len(value.strip()) > 0
    return bool(value)


def score_proof_completeness(bundle: dict) -> float:
    """Compute structural completeness score for a proof bundle.

    Returns a float between 0.0 and 1.0.
    """
    return sum(
        weight for field, weight in _WEIGHTS.items() if _is_present(bundle, field)
    )
```

**Step 4: Run tests**

```bash
python3 -m pytest tests/test_proof_scoring.py -v
```

Expected: all pass.

**Step 5: Commit**

```bash
git add api/services/proof_scoring.py tests/test_proof_scoring.py
git commit -m "feat(scoring): add proof bundle completeness scorer"
```

---

## Phase 2: API Routes (Tasks 4-7)

### Task 4: MergeGate Router Skeleton + Task Listing

**Files:**
- Create: `api/routes/mergegate.py`
- Modify: `api/main.py` (register router)
- Test: `tests/test_mergegate_routes.py`

**Step 1: Write failing tests**

```python
# tests/test_mergegate_routes.py
"""Tests for MergeGate API routes."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from api.deps import get_current_agent
from api.main import app


@pytest.fixture
def mock_agent():
    return {
        "id": "01234567-0123-7890-abcd-0123456789ab",
        "agent_name": "TestAgent",
        "model": "test-model",
    }


@pytest.fixture
async def mg_client(mock_agent):
    """Test client with auth override and mocked DB for MergeGate routes."""
    from httpx import AsyncClient, ASGITransport

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=0)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_cm)
    app.state.pool = mock_pool

    app.dependency_overrides[get_current_agent] = lambda: mock_agent

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac, mock_conn

    app.dependency_overrides.pop(get_current_agent, None)


class TestTaskListing:
    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=0)

        resp = await client.get("/api/v1/mergegate/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_tasks_returns_items(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetch = AsyncMock(return_value=[
            {
                "id": "mg_task_0001",
                "title": "Fix Cache Bug",
                "difficulty": "medium",
                "category": ["bugfix"],
                "is_solvable": True,
                "max_duration_s": 600,
            }
        ])
        mock_conn.fetchval = AsyncMock(return_value=1)

        resp = await client.get("/api/v1/mergegate/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == "mg_task_0001"
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_mergegate_routes.py::TestTaskListing -v
```

Expected: 404 — route does not exist.

**Step 3: Create router and register it**

```python
# api/routes/mergegate.py
"""MergeGate routes — delegation benchmark for coding agents.

Provides endpoints for tasks, sessions, submissions, results, and reflections.
Parallel to v3.1 submission routes. Shares auth and agent infrastructure.
"""

from typing import Annotated

import asyncpg
import structlog
from fastapi import APIRouter, Depends, Query

from api.deps import get_current_agent, get_db
from api.models.common import PaginatedResponse
from api.models.mergegate import TaskSummary

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/mergegate", tags=["mergegate"])


@router.get("/tasks", response_model=PaginatedResponse[TaskSummary])
async def list_tasks(
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
    difficulty: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> PaginatedResponse[TaskSummary]:
    """List available MergeGate tasks."""
    where_clauses = ["is_active = TRUE"]
    params: list = []
    idx = 1

    if difficulty:
        where_clauses.append(f"difficulty = ${idx}")
        params.append(difficulty)
        idx += 1

    if category:
        where_clauses.append(f"${idx} = ANY(category)")
        params.append(category)
        idx += 1

    where = " AND ".join(where_clauses)
    params.extend([limit, offset])

    rows = await db.fetch(
        f"""
        SELECT id, title, difficulty, category, is_solvable, max_duration_s
        FROM mg_tasks
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
    )

    count_params = params[: idx - 1]
    total = await db.fetchval(
        f"SELECT COUNT(*) FROM mg_tasks WHERE {where}",
        *count_params,
    )

    items = [
        TaskSummary(
            id=r["id"],
            title=r["title"],
            difficulty=r["difficulty"],
            category=list(r["category"]),
            is_solvable=r["is_solvable"],
            max_duration_s=r["max_duration_s"],
        )
        for r in rows
    ]

    return PaginatedResponse(items=items, total=total or 0, limit=limit, offset=offset)
```

Add to `api/main.py` imports and router registration:

```python
from api.routes.mergegate import router as mergegate_router
# ... in lifespan or after app creation:
app.include_router(mergegate_router)
```

**Step 4: Run tests**

```bash
python3 -m pytest tests/test_mergegate_routes.py::TestTaskListing -v
```

Expected: all pass.

**Step 5: Commit**

```bash
git add api/routes/mergegate.py api/main.py tests/test_mergegate_routes.py
git commit -m "feat(api): add MergeGate router with task listing"
```

---

### Task 5: Session Creation Endpoint

**Files:**
- Modify: `api/routes/mergegate.py`
- Test: `tests/test_mergegate_routes.py` (add TestSessionCreation class)

**Step 1: Write failing tests**

Add to `tests/test_mergegate_routes.py`:

```python
class TestSessionCreation:
    @pytest.mark.asyncio
    async def test_create_session_with_task_id(self, mg_client, mock_agent):
        client, mock_conn = mg_client

        # Mock: variant exists for this task
        mock_conn.fetchrow = AsyncMock(side_effect=[
            # First call: fetch variant
            {
                "id": "mg_task_0001_v001",
                "task_id": "mg_task_0001",
                "spec_text": "Fix the cache bug...",
                "resolved_checks": {"tests": ["pytest"]},
                "repo_snapshot": "file:///var/variants/mg_task_0001_v001.tar.gz",
            },
            # Second call: fetch task for max_duration_s
            {
                "max_duration_s": 600,
            },
        ])
        mock_conn.execute = AsyncMock()

        resp = await client.post(
            "/api/v1/mergegate/sessions",
            json={"task_id": "mg_task_0001"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "session_id" in data
        assert data["variant_id"] == "mg_task_0001_v001"
        assert "spec_text" in data
        assert "deadline" in data

    @pytest.mark.asyncio
    async def test_create_session_no_variant_404(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetchrow = AsyncMock(return_value=None)

        resp = await client.post(
            "/api/v1/mergegate/sessions",
            json={"task_id": "mg_task_9999"},
        )
        assert resp.status_code == 404
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_mergegate_routes.py::TestSessionCreation -v
```

Expected: 405 or 404 — endpoint does not exist.

**Step 3: Implement session creation endpoint**

Add to `api/routes/mergegate.py`:

```python
from datetime import datetime, timezone, timedelta
from fastapi import status
from api.models.mergegate import (
    CreateSessionRequest,
    SessionCreatedResponse,
    PredictionPayload,
)
from api.utils import generate_uuid7


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionCreatedResponse,
)
async def create_session(
    body: CreateSessionRequest,
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
) -> SessionCreatedResponse:
    """Create a new MergeGate session.

    Server selects a variant for the requested task (or picks one via
    adaptive matching if use_next=True). Agent does not choose variants
    directly.
    """
    from api.errors import AppError

    agent_id = agent["id"]

    if body.use_next:
        variant = await db.fetchrow(
            """
            SELECT v.id, v.task_id, v.spec_text, v.resolved_checks, v.repo_snapshot
            FROM mg_task_variants v
            JOIN mg_tasks t ON v.task_id = t.id
            WHERE v.is_active = TRUE AND t.is_active = TRUE
            ORDER BY RANDOM()
            LIMIT 1
            """,
        )
    elif body.task_id:
        variant = await db.fetchrow(
            """
            SELECT v.id, v.task_id, v.spec_text, v.resolved_checks, v.repo_snapshot
            FROM mg_task_variants v
            WHERE v.task_id = $1 AND v.is_active = TRUE
            ORDER BY RANDOM()
            LIMIT 1
            """,
            body.task_id,
        )
    else:
        raise AppError("Provide task_id or set use_next=true", "BAD_REQUEST", status=400)

    if variant is None:
        raise AppError("No active variants found for task", "NOT_FOUND", status=404)

    task = await db.fetchrow(
        "SELECT max_duration_s FROM mg_tasks WHERE id = $1",
        variant["task_id"],
    )
    max_duration = task["max_duration_s"] if task else 600

    session_id = generate_uuid7()
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=max_duration)

    await db.execute(
        """
        INSERT INTO mg_sessions (id, agent_id, variant_id, status, submission_deadline_at, started_at, created_at)
        VALUES ($1, $2, $3, 'running', $4, $5, $5)
        """,
        session_id,
        agent_id,
        variant["id"],
        deadline,
        now,
    )

    # Store prediction if provided
    if body.prediction:
        pred_id = generate_uuid7()
        await db.execute(
            """
            INSERT INTO mg_predictions (id, session_id, confidence, reasoning,
                estimated_difficulty, expected_approach, known_risks)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            pred_id,
            session_id,
            body.prediction.confidence,
            body.prediction.reasoning,
            body.prediction.estimated_difficulty,
            body.prediction.expected_approach,
            body.prediction.known_risks,
        )

    # Record session_created event
    event_id = generate_uuid7()
    await db.execute(
        """
        INSERT INTO mg_session_events (id, session_id, event_type, event_data)
        VALUES ($1, $2, 'session_created', $3)
        """,
        event_id,
        session_id,
        {"variant_id": variant["id"], "agent_id": str(agent_id)},
    )

    return SessionCreatedResponse(
        session_id=str(session_id),
        variant_id=variant["id"],
        spec_text=variant["spec_text"],
        repo_download_url=f"/api/v1/mergegate/sessions/{session_id}/repo",
        deadline=deadline.isoformat(),
        submission_contract={
            "submit_url": f"/api/v1/mergegate/sessions/{session_id}/submit",
            "result_url": f"/api/v1/mergegate/sessions/{session_id}/result",
            "reflect_url": f"/api/v1/mergegate/sessions/{session_id}/reflect",
        },
    )
```

**Step 4: Run tests**

```bash
python3 -m pytest tests/test_mergegate_routes.py::TestSessionCreation -v
```

Expected: all pass.

**Step 5: Commit**

```bash
git add api/routes/mergegate.py tests/test_mergegate_routes.py
git commit -m "feat(api): add MergeGate session creation endpoint"
```

---

### Task 6: Submit Endpoint

**Files:**
- Modify: `api/routes/mergegate.py`
- Test: `tests/test_mergegate_routes.py` (add TestSubmission class)

This follows the same TDD pattern: write failing test for `POST /sessions/{id}/submit`, then implement. The endpoint:

1. Validates session exists, belongs to agent, is in `running` status
2. Inserts into `mg_submissions` and `mg_proof_bundles`
3. Updates session status to `submitted`
4. Triggers async scoring (placeholder for Task 8)
5. Returns `SessionSubmittedResponse`

---

### Task 7: Result + Reflect + Recent Endpoints

**Files:**
- Modify: `api/routes/mergegate.py`
- Test: `tests/test_mergegate_routes.py`

Three simple endpoints following the same TDD pattern:

- `GET /sessions/{id}/result` — fetch from mg_results, return SessionResultResponse
- `POST /sessions/{id}/reflect` — insert into mg_reflections
- `GET /sessions/recent` — paginated listing from mg_sessions + mg_results

---

## Phase 3: Scoring Pipeline (Tasks 8-10)

### Task 8: Scoring Pipeline Service

**Files:**
- Create: `api/services/mergegate_scoring.py`
- Test: `tests/test_mergegate_scoring.py`

The scoring pipeline:
1. Fetches variant's `resolved_checks`
2. Applies patch to clean repo copy
3. Runs hidden test suite commands
4. Runs regression checks
5. Computes proof completeness (reuse Task 3)
6. Computes review cost proxy
7. Computes confidence gap
8. Determines `mergeable`, `quality_floor_passed`, `safety_floor_passed`
9. Writes mg_results row

v1 implementation: scoring runs synchronously for simplicity. Background task scheduling comes later.

---

### Task 9: Failure Annotation Pipeline

**Files:**
- Create: `api/services/failure_annotation.py`
- Test: `tests/test_failure_annotation.py`

LLM judge that classifies failures. Takes: spec, patch, check results, proof bundle. Returns: failure_class, failure_severity, failure_detail, is_silent_failure, failure_signature (sha256 of class + category + normalized detail).

v1 implementation: structured prompt to Claude API. Conservative taxonomy (9 classes + 3 severities).

---

### Task 10: Review Cost Proxy Calculator

**Files:**
- Modify: `api/services/proof_scoring.py` (add review_cost_proxy function)
- Test: `tests/test_proof_scoring.py` (add review cost tests)

Implements the formula from the design doc:

```
review_cost_proxy = 3.0
    * (1 + 0.3 * (1 - proof_completeness))
    * (1 + 0.01 * max(0, diff_lines - 50))
    * (1 + 0.1 * max(0, files_changed - 3))
```

---

## Phase 4: Task Content (Tasks 11-12)

### Task 11: First 5 MergeGate Task Environments

**Files:**
- Create: `tasks/mergegate/mg_task_0001/` (repo + metadata)
- Create: `tasks/mergegate/mg_task_0002/` through `mg_task_0005/`
- Create: `scripts/seed_mergegate_tasks.py`

5 real-ish repo environments with bugs:
1. Cache TTL comparison bug (easy)
2. Off-by-one in pagination (easy)
3. Race condition in counter (medium)
4. Spec-ambiguous API migration (medium)
5. Multi-file refactor with hidden invariants (hard)

Each task directory contains:
- `repo/` — the base codebase
- `task.json` — metadata, checks, variant schema
- `checks/` — hidden test suite and regression checks

The seed script inserts tasks and initial variants into the DB and creates tarballs.

---

### Task 12: First 3 Unsolvable Tasks

**Files:**
- Create: `tasks/mergegate/mg_task_unsolvable_001/` through `003/`

3 unsolvable/underspecified tasks:
1. Contradictory constraints (add feature without modifying existing files)
2. Insufficient specification (ambiguous acceptance criteria)
3. Impossible sandbox requirement (needs network access)

---

## Phase 5: Integration (Tasks 13-14)

### Task 13: Wire Scoring to Submit Endpoint

Connect Task 6 (submit endpoint) to Task 8 (scoring pipeline) so that
submitting a session triggers scoring and populates mg_results.

---

### Task 14: Delegation Profile Endpoint

**Files:**
- Create: `api/services/delegation_profile.py`
- Modify: `api/routes/mergegate.py` (add `/profile` endpoint)
- Test: `tests/test_delegation_profile.py`

Aggregates across all sessions for the requesting agent:
- Verified Autonomy score
- Mergeability stats
- Review cost stats
- Calibration curve data
- Failure profile
- Deployment recommendation

---

## Execution Notes

- Tasks 1-7 can be implemented first without Docker or real repos — all
  routes work against mocked DB connections.
- Tasks 8-10 need the scoring pipeline design to be more concrete for
  Docker-based repo scoring. The first implementation can use a mock scorer.
- Tasks 11-12 are content work — building the actual repo environments.
- Tasks 13-14 wire everything together.
- Run `ruff check .` after each commit.
- Run the full test suite after tasks 3, 5, 7, 10, and 14.
