"""Pydantic models for MergeGate delegation benchmark endpoints.

MergeGate benchmarks AI agents on their ability to delegate coding tasks:
predicting difficulty, producing proof bundles, and knowing when to refuse.

Models:
  CreateSessionRequest: Start a new MergeGate session (POST /sessions).
  PredictionPayload: Agent's pre-task confidence and risk assessment.
  ProofBundlePayload: Structured evidence accompanying a submission.
  SubmitRequest: Submit a patch, refusal, or clarification request.
  ReflectionRequest: Post-result self-assessment by the agent.
  SessionCreatedResponse: Returned when a session is created.
  SessionSubmittedResponse: Returned when a submission is accepted for scoring.
  SessionResultResponse: Full scoring result for a completed session.
  TaskSummary: Lightweight task metadata for listings.
  RecentSessionItem: Summary row for recent sessions list.

SECURITY: All string fields reject null bytes (SEC-08 pattern).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

# Valid difficulty tiers for tasks and predictions
_VALID_DIFFICULTIES = {"easy", "medium", "hard", "nightmare"}

# Valid submission modes
_VALID_SUBMISSION_MODES = {"patch", "refusal", "clarification_request"}

# Valid proof bundle submission modes (subset)
_VALID_PROOF_SUBMISSION_MODES = {"patch", "refusal"}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PredictionPayload(BaseModel):
    """Agent's pre-task prediction of difficulty, approach, and risks.

    Confidence is a float in [0.0, 1.0] where 1.0 means absolute certainty
    that the task will be solved correctly.
    """

    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str | None = None
    estimated_difficulty: str | None = None
    expected_approach: str | None = None
    known_risks: list[str] | None = None

    @field_validator("estimated_difficulty")
    @classmethod
    def valid_difficulty(cls, v: str | None) -> str | None:
        """Reject difficulty values outside the canonical set."""
        if v is not None and v not in _VALID_DIFFICULTIES:
            raise ValueError(
                f"Invalid difficulty: {v}. Must be one of: {', '.join(sorted(_VALID_DIFFICULTIES))}"
            )
        return v

    @field_validator("reasoning", "expected_approach", mode="after")
    @classmethod
    def no_null_bytes_str(cls, v: str | None) -> str | None:
        """SEC-08: Reject null bytes that would crash asyncpg with a 500."""
        if v is not None and "\x00" in v:
            raise ValueError("Null bytes are not allowed")
        return v


class CreateSessionRequest(BaseModel):
    """Request body for POST /api/v1/mergegate/sessions.

    Either provide a specific task_id or set use_next=True to get the
    next recommended task based on the agent's skill level.
    """

    task_id: str | None = None
    use_next: bool = False
    prediction: PredictionPayload | None = None

    @field_validator("task_id", mode="after")
    @classmethod
    def no_null_bytes_task_id(cls, v: str | None) -> str | None:
        """SEC-08: Reject null bytes that would crash asyncpg with a 500."""
        if v is not None and "\x00" in v:
            raise ValueError("Null bytes are not allowed")
        return v


class ProofBundlePayload(BaseModel):
    """Structured evidence bundle accompanying a MergeGate submission.

    Contains test results, changed files, assumptions, correctness argument,
    rollback plan, and residual risks. Submissions without adequate proof
    bundles score lower on proof_completeness.
    """

    schema_version: str
    submission_mode: str = "patch"
    tests_run: list[dict[str, Any]] | None = None
    files_changed: list[dict[str, Any]] | None = None
    assumptions: list[str] | None = None
    not_verified: list[str] | None = None
    correctness_argument: str | None = None
    rollback_plan: str | None = None
    residual_risks: list[str] | None = None
    final_confidence: float | None = Field(None, ge=0.0, le=1.0)

    @field_validator("submission_mode")
    @classmethod
    def valid_proof_submission_mode(cls, v: str) -> str:
        """Reject submission modes not valid for proof bundles."""
        if v not in _VALID_PROOF_SUBMISSION_MODES:
            raise ValueError(
                f"Invalid submission_mode: {v}. Must be one of: {', '.join(sorted(_VALID_PROOF_SUBMISSION_MODES))}"
            )
        return v

    @field_validator(
        "schema_version",
        "correctness_argument",
        "rollback_plan",
        mode="after",
    )
    @classmethod
    def no_null_bytes_str(cls, v: str | None) -> str | None:
        """SEC-08: Reject null bytes that would crash asyncpg with a 500."""
        if v is not None and "\x00" in v:
            raise ValueError("Null bytes are not allowed")
        return v


class SubmitRequest(BaseModel):
    """Request body for POST /api/v1/mergegate/sessions/{id}/submit.

    Supports three submission modes:
      - patch: Agent provides a git diff and proof bundle.
      - refusal: Agent declines the task with justification.
      - clarification_request: Agent asks for more information.
    """

    submission_mode: str
    patch_text: str | None = None
    patch_format: str = "git_diff"
    submission_notes: str | None = None
    proof_bundle: ProofBundlePayload

    @field_validator("submission_mode")
    @classmethod
    def valid_submission_mode(cls, v: str) -> str:
        """Reject submission modes outside the canonical set."""
        if v not in _VALID_SUBMISSION_MODES:
            raise ValueError(
                f"Invalid submission_mode: {v}. Must be one of: {', '.join(sorted(_VALID_SUBMISSION_MODES))}"
            )
        return v

    @field_validator("patch_text", "submission_notes", "patch_format", mode="after")
    @classmethod
    def no_null_bytes_str(cls, v: str | None) -> str | None:
        """SEC-08: Reject null bytes that would crash asyncpg with a 500."""
        if v is not None and "\x00" in v:
            raise ValueError("Null bytes are not allowed")
        return v


class ReflectionRequest(BaseModel):
    """Post-result self-assessment submitted by the agent.

    Captures whether the agent was surprised by the outcome, what it
    thinks went wrong, and what it would change on a retry.
    """

    was_surprised: bool | None = None
    failure_explanation: str | None = None
    root_cause_guess: str | None = None
    would_change: str | None = None
    updated_confidence: float | None = Field(None, ge=0.0, le=1.0)

    @field_validator(
        "failure_explanation",
        "root_cause_guess",
        "would_change",
        mode="after",
    )
    @classmethod
    def no_null_bytes_str(cls, v: str | None) -> str | None:
        """SEC-08: Reject null bytes that would crash asyncpg with a 500."""
        if v is not None and "\x00" in v:
            raise ValueError("Null bytes are not allowed")
        return v


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class SessionCreatedResponse(BaseModel):
    """Response returned when a MergeGate session is created (HTTP 201).

    Contains the task spec, repo download URL, deadline, and the
    submission contract describing what fields are required.
    """

    session_id: str
    variant_id: str
    spec_text: str
    repo_download_url: str | None = None
    deadline: str | None = None
    submission_contract: dict[str, Any] | None = None


class SessionSubmittedResponse(BaseModel):
    """Response returned when a submission is accepted for scoring (HTTP 202).

    The submission has been queued for evaluation. Poll the result
    endpoint or wait for scoring_eta_s seconds.
    """

    session_id: str
    status: str
    scoring_eta_s: int = 30


class SessionResultResponse(BaseModel):
    """Full scoring result for a completed MergeGate session.

    All fields except session_id and status are nullable until the
    session reaches 'completed' status.
    """

    session_id: str
    status: str  # pending | scoring | completed | error

    # Task outcome
    task_success: bool | None = None
    mergeable: bool | None = None
    hidden_tests_passed: int | None = None
    hidden_tests_total: int | None = None
    regressions_found: int | None = None

    # Quality metrics
    approval_proxy_score: float | None = None
    proof_completeness: float | None = None
    review_cost_proxy: float | None = None

    # Confidence tracking
    confidence_declared: float | None = None
    confidence_gap: float | None = None

    # Failure analysis
    failure_class: str | None = None
    failure_severity: str | None = None
    failure_detail: str | None = None
    failure_signature: str | None = None
    is_silent_failure: bool | None = None

    # Refusal scoring
    correctly_refused: bool | None = None
    refusal_quality: float | None = None

    # Floor checks
    quality_floor_passed: bool | None = None
    safety_floor_passed: bool | None = None

    scored_at: str | None = None


class TaskSummary(BaseModel):
    """Lightweight task metadata for task listings.

    Used in GET /api/v1/mergegate/tasks responses.
    """

    id: str
    title: str
    difficulty: str
    category: list[str]
    max_duration_s: int

    @field_validator("difficulty")
    @classmethod
    def valid_difficulty(cls, v: str) -> str:
        """Reject difficulty values outside the canonical set."""
        if v not in _VALID_DIFFICULTIES:
            raise ValueError(
                f"Invalid difficulty: {v}. Must be one of: {', '.join(sorted(_VALID_DIFFICULTIES))}"
            )
        return v


class RecentSessionItem(BaseModel):
    """Summary row for GET /api/v1/mergegate/sessions/recent.

    Lightweight response for the recent sessions list — no full
    scoring details, just key outcome indicators.
    """

    session_id: str
    variant_id: str
    status: str
    task_success: bool | None = None
    mergeable: bool | None = None
    proof_completeness: float | None = None
    created_at: str
