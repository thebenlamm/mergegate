"""MergeGate delegation benchmark routes.

Provides task browsing and session creation endpoints for the MergeGate
parallel product:
  GET  /api/v1/mergegate/tasks          — paginated task listing with filters
  GET  /api/v1/mergegate/tasks/{id}     — task detail (metadata only)
  POST /api/v1/mergegate/sessions       — create a new session

SECURITY: These routes never expose spec_text or variant_id in browsing
endpoints. Those are only revealed at session creation time to prevent
cherry-picking.

All endpoints require Bearer token auth via Depends(get_current_agent).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import asyncpg
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.deps import get_current_agent, get_db
from api.services.mg_scorer import score_session
from api.errors import AppError
from api.models.common import PaginatedResponse
from api.models.mergegate import (
    CreateSessionRequest,
    RecentSessionItem,
    ReflectionRequest,
    SessionCreatedResponse,
    SessionResultResponse,
    SessionSubmittedResponse,
    SubmitRequest,
    TaskSummary,
)
from api.utils import generate_uuid7

router = APIRouter(prefix="/api/v1/mergegate", tags=["mergegate"])

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Response models (detail-level, kept alongside the router)
# ---------------------------------------------------------------------------


class TaskDetailResponse(BaseModel):
    """Full task metadata for the detail endpoint.

    Includes template-level description but NOT spec_text or variant_id.
    Those are only revealed when a session is created.
    """

    id: str
    title: str
    difficulty: str
    category: list[str]
    description: str
    max_duration_s: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/tasks", response_model=PaginatedResponse[TaskSummary])
async def list_tasks(
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
    difficulty: str | None = Query(
        default=None, description="Filter by difficulty: easy, medium, hard, nightmare"
    ),
    category: str | None = Query(
        default=None, description="Comma-separated category filters (OR overlap)"
    ),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[TaskSummary]:
    """List active MergeGate tasks with optional filtering.

    Returns paginated TaskSummary items. Never includes spec_text,
    variant_id, or any session-specific data.
    """
    conditions: list[str] = ["t.is_active = TRUE"]
    params: list = []
    param_idx = 1

    if difficulty is not None:
        conditions.append(f"t.difficulty = ${param_idx}")
        params.append(difficulty)
        param_idx += 1

    if category is not None:
        categories = [c.strip() for c in category.split(",") if c.strip()]
        if categories:
            conditions.append(f"t.category && ${param_idx}::TEXT[]")
            params.append(categories)
            param_idx += 1

    where_clause = " AND ".join(conditions)

    # Count query
    count_query = f"SELECT COUNT(*) FROM mg_tasks t WHERE {where_clause}"
    total = await db.fetchval(count_query, *params)

    # Paginated data query
    params.append(limit)
    limit_idx = param_idx
    param_idx += 1

    params.append(offset)
    offset_idx = param_idx

    rows = await db.fetch(
        f"""
        SELECT
            t.id,
            t.title,
            t.difficulty,
            t.category,
            t.max_duration_s
        FROM mg_tasks t
        WHERE {where_clause}
        ORDER BY t.id ASC
        LIMIT ${limit_idx} OFFSET ${offset_idx}
        """,
        *params,
    )

    items = [
        TaskSummary(
            id=row["id"],
            title=row["title"],
            difficulty=row["difficulty"],
            category=list(row["category"]),
            max_duration_s=row["max_duration_s"],
        )
        for row in rows
    ]

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/tasks/{task_id}", response_model=TaskDetailResponse)
async def get_task_detail(
    task_id: str,
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
) -> TaskDetailResponse:
    """Get detail for a specific MergeGate task.

    Returns template-level metadata including description. Does NOT
    include spec_text or variant_id — those are only revealed when
    a session is created (anti-cherry-picking).

    Returns 404 if task not found or is inactive.
    """
    row = await db.fetchrow(
        """
        SELECT
            t.id,
            t.title,
            t.difficulty,
            t.category,
            t.description,
            t.max_duration_s
        FROM mg_tasks t
        WHERE t.id = $1
          AND t.is_active = TRUE
        """,
        task_id,
    )

    if row is None:
        raise AppError(
            "Task not found or is no longer active.",
            "NOT_FOUND",
            status=404,
        )

    return TaskDetailResponse(
        id=row["id"],
        title=row["title"],
        difficulty=row["difficulty"],
        category=list(row["category"]),
        description=row["description"],
        max_duration_s=row["max_duration_s"],
    )


@router.post(
    "/sessions",
    response_model=SessionCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    body: CreateSessionRequest,
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
) -> SessionCreatedResponse:
    """Create a new MergeGate session.

    Selects a variant (either for a specific task or the next recommended one),
    creates a session row, optionally records the agent's prediction, and
    returns the task spec with a deadline.

    Returns 400 if neither task_id nor use_next is provided.
    Returns 404 if no active variant is found.
    """
    agent_id = agent["id"]

    # ── Validate request ────────────────────────────────────────────────
    if not body.task_id and not body.use_next:
        raise AppError(
            "Provide either task_id or set use_next=true.",
            "BAD_REQUEST",
            status=400,
        )

    # ── Select a variant ────────────────────────────────────────────────
    if body.use_next:
        variant = await db.fetchrow(
            """
            SELECT v.id, v.task_id, v.spec_text, v.resolved_checks, v.repo_snapshot
            FROM mg_task_variants v
            JOIN mg_tasks t ON t.id = v.task_id
            WHERE t.is_active = TRUE
            ORDER BY RANDOM()
            LIMIT 1
            """,
        )
    else:
        variant = await db.fetchrow(
            """
            SELECT v.id, v.task_id, v.spec_text, v.resolved_checks, v.repo_snapshot
            FROM mg_task_variants v
            JOIN mg_tasks t ON t.id = v.task_id
            WHERE v.task_id = $1
              AND t.is_active = TRUE
            ORDER BY RANDOM()
            LIMIT 1
            """,
            body.task_id,
        )

    if variant is None:
        raise AppError(
            "No active variant found for the requested task.",
            "NOT_FOUND",
            status=404,
        )

    # ── Look up task duration ───────────────────────────────────────────
    task_row = await db.fetchrow(
        "SELECT max_duration_s FROM mg_tasks WHERE id = $1",
        variant["task_id"],
    )
    max_duration_s = task_row["max_duration_s"] if task_row else 600

    # ── Create session ──────────────────────────────────────────────────
    session_id = str(generate_uuid7())
    now = datetime.now(timezone.utc)
    deadline = now + timedelta(seconds=max_duration_s)

    await db.execute(
        """
        INSERT INTO mg_sessions (id, agent_id, variant_id, status, submission_deadline_at, started_at, created_at)
        VALUES ($1, $2, $3, 'running', $4, $5, $6)
        """,
        session_id,
        agent_id,
        variant["id"],
        deadline,
        now,
        now,
    )

    # ── Record prediction (optional) ────────────────────────────────────
    if body.prediction is not None:
        prediction_id = str(generate_uuid7())
        await db.execute(
            """
            INSERT INTO mg_predictions
                (id, session_id, confidence, reasoning, estimated_difficulty,
                 expected_approach, known_risks, predicted_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            prediction_id,
            session_id,
            body.prediction.confidence,
            body.prediction.reasoning,
            body.prediction.estimated_difficulty,
            body.prediction.expected_approach,
            json.dumps(body.prediction.known_risks) if body.prediction.known_risks else None,
            now,
        )

    # ── Record session_created event ────────────────────────────────────
    event_id = str(generate_uuid7())
    await db.execute(
        """
        INSERT INTO mg_session_events (id, session_id, event_type, occurred_at)
        VALUES ($1, $2, 'session_created', $3)
        """,
        event_id,
        session_id,
        now,
    )

    # ── Build response ──────────────────────────────────────────────────
    repo_download_url = f"/api/v1/mergegate/sessions/{session_id}/repo"
    submission_contract = {
        "submit_url": f"/api/v1/mergegate/sessions/{session_id}/submit",
        "result_url": f"/api/v1/mergegate/sessions/{session_id}/result",
        "reflect_url": f"/api/v1/mergegate/sessions/{session_id}/reflect",
    }

    return SessionCreatedResponse(
        session_id=session_id,
        variant_id=variant["id"],
        spec_text=variant["spec_text"],
        repo_download_url=repo_download_url,
        deadline=deadline.isoformat(),
        submission_contract=submission_contract,
    )


@router.get("/sessions/recent", response_model=PaginatedResponse[RecentSessionItem])
async def list_recent_sessions(
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse[RecentSessionItem]:
    """List the requesting agent's recent MergeGate sessions.

    Returns paginated sessions ordered by creation time (newest first),
    with scoring data included when available via LEFT JOIN on mg_results.
    """
    agent_id = agent["id"]

    total = await db.fetchval(
        "SELECT COUNT(*) FROM mg_sessions WHERE agent_id = $1",
        agent_id,
    )

    rows = await db.fetch(
        """
        SELECT
            s.id,
            s.variant_id,
            s.status,
            r.task_success,
            r.mergeable,
            r.proof_completeness,
            s.created_at
        FROM mg_sessions s
        LEFT JOIN mg_results r ON r.session_id = s.id
        WHERE s.agent_id = $1
        ORDER BY s.created_at DESC
        LIMIT $2 OFFSET $3
        """,
        agent_id,
        limit,
        offset,
    )

    items = [
        RecentSessionItem(
            session_id=str(row["id"]),
            variant_id=row["variant_id"],
            status=row["status"],
            task_success=row["task_success"],
            mergeable=row["mergeable"],
            proof_completeness=float(row["proof_completeness"])
            if row["proof_completeness"] is not None
            else None,
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/sessions/{session_id}/result", response_model=SessionResultResponse)
async def get_session_result(
    session_id: str,
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
) -> SessionResultResponse:
    """Get the scoring result for a MergeGate session.

    Returns the full scoring breakdown if the session is completed,
    or a minimal response with just session_id and status if scoring
    is still pending.

    Returns 404 if session not found or does not belong to the agent.
    """
    agent_id = agent["id"]

    session = await db.fetchrow(
        "SELECT id, agent_id, status FROM mg_sessions WHERE id = $1",
        session_id,
    )

    if session is None or str(session["agent_id"]) != str(agent_id):
        raise AppError(
            "Session not found.",
            "NOT_FOUND",
            status=404,
        )

    result = await db.fetchrow(
        "SELECT * FROM mg_results WHERE session_id = $1",
        session_id,
    )

    if result is None:
        return SessionResultResponse(
            session_id=str(session["id"]),
            status=session["status"],
        )

    return SessionResultResponse(
        session_id=str(session["id"]),
        status=session["status"],
        task_success=result["task_success"],
        mergeable=result["mergeable"],
        hidden_tests_passed=result["hidden_tests_passed"],
        hidden_tests_total=result["hidden_tests_total"],
        regressions_found=result["regressions_found"],
        approval_proxy_score=float(result["approval_proxy_score"])
        if result["approval_proxy_score"] is not None
        else None,
        proof_completeness=float(result["proof_completeness"])
        if result["proof_completeness"] is not None
        else None,
        review_cost_proxy=float(result["review_cost_proxy"])
        if result["review_cost_proxy"] is not None
        else None,
        confidence_declared=float(result["confidence_declared"])
        if result["confidence_declared"] is not None
        else None,
        confidence_gap=float(result["confidence_gap"])
        if result["confidence_gap"] is not None
        else None,
        failure_class=result["failure_class"],
        failure_severity=result["failure_severity"],
        failure_detail=result["failure_detail"],
        failure_signature=result["failure_signature"],
        is_silent_failure=result["is_silent_failure"],
        correctly_refused=result["correctly_refused"],
        refusal_quality=float(result["refusal_quality"])
        if result["refusal_quality"] is not None
        else None,
        quality_floor_passed=result["quality_floor_passed"],
        safety_floor_passed=result["safety_floor_passed"],
        scored_at=str(result["scored_at"]) if result["scored_at"] is not None else None,
    )


@router.post("/sessions/{session_id}/reflect")
async def reflect_on_session(
    session_id: str,
    body: ReflectionRequest,
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
) -> dict:
    """Submit a post-result self-assessment for a MergeGate session.

    The agent reflects on the outcome: whether it was surprised, what
    went wrong, root cause analysis, and what it would change.

    Session must be in 'submitted', 'scoring', or 'completed' status.
    Returns 404 if session not found or not owned by agent.
    Returns 409 if session is still in 'running' status.
    """
    agent_id = agent["id"]

    session = await db.fetchrow(
        "SELECT id, agent_id, status FROM mg_sessions WHERE id = $1",
        session_id,
    )

    if session is None or str(session["agent_id"]) != str(agent_id):
        raise AppError(
            "Session not found.",
            "NOT_FOUND",
            status=404,
        )

    allowed_statuses = {"submitted", "scoring", "completed"}
    if session["status"] not in allowed_statuses:
        raise AppError(
            "Reflection is only allowed after submission or completion.",
            "SESSION_NOT_READY",
            status=409,
        )

    now = datetime.now(timezone.utc)
    reflection_id = str(generate_uuid7())

    await db.execute(
        """
        INSERT INTO mg_reflections
            (id, session_id, was_surprised, failure_explanation,
             root_cause_guess, would_change, updated_confidence, reflected_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
        reflection_id,
        session_id,
        body.was_surprised,
        body.failure_explanation,
        body.root_cause_guess,
        body.would_change,
        body.updated_confidence,
        now,
    )

    return {"status": "ok"}


@router.post(
    "/sessions/{session_id}/submit",
    response_model=SessionSubmittedResponse,
)
async def submit_session(
    session_id: str,
    body: SubmitRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
) -> SessionSubmittedResponse:
    """Submit a patch, refusal, or clarification request for a MergeGate session.

    Validates the session belongs to the requesting agent and is in 'running'
    status. Inserts the submission and proof bundle, updates session status
    to 'submitted', and records a session event.

    Returns 404 if session not found or does not belong to agent.
    Returns 409 if session is not in 'running' status.
    """
    agent_id = agent["id"]

    # ── Fetch and validate session ─────────────────────────────────────
    session = await db.fetchrow(
        "SELECT id, agent_id, status, variant_id FROM mg_sessions WHERE id = $1",
        session_id,
    )

    if session is None or str(session["agent_id"]) != str(agent_id):
        raise AppError(
            "Session not found.",
            "NOT_FOUND",
            status=404,
        )

    if session["status"] != "running":
        raise AppError(
            "Session has already been submitted or completed.",
            "ALREADY_SUBMITTED",
            status=409,
        )

    now = datetime.now(timezone.utc)

    # ── Atomic write: submission + proof bundle + status + event ─────────
    async with db.transaction():
        submission_id = str(generate_uuid7())
        await db.execute(
            """
            INSERT INTO mg_submissions
                (id, session_id, submission_mode, patch_text, patch_format,
                 submission_notes, submitted_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            submission_id,
            session_id,
            body.submission_mode,
            body.patch_text,
            body.patch_format,
            body.submission_notes,
            now,
        )

        bundle_id = str(generate_uuid7())
        pb = body.proof_bundle
        await db.execute(
            """
            INSERT INTO mg_proof_bundles
                (id, session_id, schema_version, tests_run, files_changed,
                 assumptions_json, not_verified_json, residual_risks_json,
                 correctness_argument, rollback_plan, final_confidence,
                 raw_bundle, submitted_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            bundle_id,
            session_id,
            pb.schema_version,
            json.dumps(pb.tests_run) if pb.tests_run else None,
            json.dumps(pb.files_changed) if pb.files_changed else None,
            json.dumps(pb.assumptions) if pb.assumptions else None,
            json.dumps(pb.not_verified) if pb.not_verified else None,
            json.dumps(pb.residual_risks) if pb.residual_risks else None,
            pb.correctness_argument,
            pb.rollback_plan,
            pb.final_confidence,
            json.dumps(pb.model_dump()),
            now,
        )

        await db.execute(
            """
            UPDATE mg_sessions
            SET status = 'scoring', submitted_at = $2
            WHERE id = $1
            """,
            session_id,
            now,
        )

        event_id = str(generate_uuid7())
        await db.execute(
            """
            INSERT INTO mg_session_events (id, session_id, event_type, occurred_at)
            VALUES ($1, $2, 'patch_submitted', $3)
            """,
            event_id,
            session_id,
            now,
        )

    # ── Schedule background scoring ───────────────────────────────────
    background_tasks.add_task(
        score_session,
        session_id=session_id,
        pool=request.app.state.pool,
    )

    return SessionSubmittedResponse(
        session_id=session_id,
        status="scoring",
    )


# ---------------------------------------------------------------------------
# GET /sessions/{session_id}/repo — Download variant repo tarball
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/repo")
async def download_repo(
    session_id: str,
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
) -> FileResponse:
    """Serve the variant repo tarball for a session.

    Validates session ownership. Returns the tarball as application/gzip.
    """
    agent_id = agent["id"]

    row = await db.fetchrow(
        """
        SELECT s.agent_id, v.repo_snapshot
        FROM mg_sessions s
        JOIN mg_task_variants v ON v.id = s.variant_id
        WHERE s.id = $1
        """,
        session_id,
    )

    if row is None or str(row["agent_id"]) != str(agent_id):
        raise AppError("Session not found.", "NOT_FOUND", status=404)

    repo_path = row["repo_snapshot"]
    if not repo_path or not Path(repo_path).exists():
        raise AppError("Repo snapshot not available.", "NOT_FOUND", status=404)

    return FileResponse(
        path=repo_path,
        media_type="application/gzip",
        filename=f"{session_id}.tar.gz",
    )


# ---------------------------------------------------------------------------
# GET /profile — Agent's delegation profile
# ---------------------------------------------------------------------------


@router.get("/profile")
async def get_delegation_profile(
    agent: Annotated[dict, Depends(get_current_agent)],
    db: Annotated[asyncpg.Connection, Depends(get_db)],
) -> dict:
    """Get the authenticated agent's delegation profile.

    Aggregates all scored MergeGate sessions into mergeability,
    review cost, calibration, and failure metrics.
    """
    agent_id = agent["id"]

    rows = await db.fetch(
        """
        SELECT
            r.task_success, r.hidden_tests_passed, r.hidden_tests_total,
            r.regressions_found, r.mergeable, r.approval_proxy_score,
            r.proof_completeness, r.review_cost_proxy,
            r.confidence_declared, r.confidence_gap,
            r.failure_class, r.is_silent_failure,
            r.correctly_refused, r.refusal_quality,
            t.is_solvable
        FROM mg_results r
        JOIN mg_sessions s ON s.id = r.session_id
        JOIN mg_task_variants v ON v.id = s.variant_id
        JOIN mg_tasks t ON t.id = v.task_id
        WHERE s.agent_id = $1
        ORDER BY r.scored_at
        """,
        agent_id,
    )

    if not rows:
        return {
            "agent_name": agent["agent_name"],
            "model": agent["model"],
            "total_sessions": 0,
            "message": "No scored MergeGate sessions yet.",
        }

    total = len(rows)
    successes = sum(1 for r in rows if r["task_success"])

    solvable = [r for r in rows if r["is_solvable"]]
    unsolvable = [r for r in rows if not r["is_solvable"]]
    solvable_mergeables = sum(1 for r in solvable if r["mergeable"])

    review_costs = [r["review_cost_proxy"] for r in rows if r["review_cost_proxy"] is not None]
    proof_scores = [r["proof_completeness"] for r in rows if r["proof_completeness"] is not None]
    gaps = [r["confidence_gap"] for r in rows if r["confidence_gap"] is not None]

    total_review_min = sum(review_costs) if review_costs else 0
    va = (solvable_mergeables / total_review_min * 60) if total_review_min > 0 else 0

    failure_classes = {}
    for r in rows:
        fc = r["failure_class"]
        if fc:
            failure_classes[fc] = failure_classes.get(fc, 0) + 1

    return {
        "agent_name": agent["agent_name"],
        "model": agent["model"],
        "total_sessions": total,
        "verified_autonomy": round(va, 2),
        "mergeability": {
            "task_success_rate": round(successes / total, 3) if total else 0,
            "mergeability_rate": round(solvable_mergeables / len(solvable), 3) if solvable else 0,
            "regressions": sum(
                1 for r in rows if r["regressions_found"] and r["regressions_found"] > 0
            ),
        },
        "review_cost": {
            "median_minutes": round(sorted(review_costs)[len(review_costs) // 2], 2)
            if review_costs
            else None,
            "mean_proof_completeness": round(sum(proof_scores) / len(proof_scores), 3)
            if proof_scores
            else None,
        },
        "calibration": {
            "mean_absolute_gap": round(sum(abs(g) for g in gaps) / len(gaps), 3) if gaps else None,
            "overconfidence_rate": round(sum(1 for g in gaps if g > 0.2) / len(gaps), 3)
            if gaps
            else None,
        },
        "know_nothing": {
            "unsolvable_sessions": len(unsolvable),
            "correct_refusals": sum(1 for r in unsolvable if r["correctly_refused"]),
            "score": round(
                sum(1 for r in unsolvable if r["correctly_refused"]) / len(unsolvable), 3
            )
            if unsolvable
            else None,
        },
        "failure_profile": failure_classes,
        "silent_failures": sum(1 for r in rows if r["is_silent_failure"]),
    }
