"""MergeGate scoring pipeline.

Applies a submitted patch to a clean repo snapshot, runs hidden checks,
and computes all mg_results columns. Called as a background task after
an agent submits via POST /sessions/{id}/submit.

Follows the same pool-acquisition pattern as submission_pipeline.py:
the background task acquires its own connection from the pool.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tarfile
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog

from api.services.proof_scoring import estimate_review_cost, score_proof_completeness
from api.utils import generate_uuid7

logger = structlog.get_logger(__name__)

SCORING_VERSION = "mg_scorer_v1"

# Approval proxy thresholds
_APPROVAL_THRESHOLD = 0.6
_PROOF_COMPLETENESS_FLOOR = 0.5


# ---------------------------------------------------------------------------
# Data classes for intermediate results
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Result of running a single check command."""

    command: str
    check_type: str  # "correctness" or "regression"
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False


@dataclass
class PatchResult:
    """Result of applying a patch to the repo."""

    applied: bool
    stderr: str = ""


@dataclass
class ScoringOutcome:
    """All computed fields for mg_results."""

    task_success: bool
    hidden_tests_passed: int
    hidden_tests_total: int
    regressions_found: int
    mergeable: bool | None
    approval_proxy_score: float | None
    proof_completeness: float | None
    review_cost_proxy: float | None
    review_cost_confidence: str | None
    confidence_declared: float | None
    confidence_gap: float | None
    failure_class: str | None
    failure_severity: str | None
    failure_detail: str | None
    failure_signature: str | None
    is_silent_failure: bool
    correctly_refused: bool | None
    refusal_quality: float | None
    quality_floor_passed: bool
    safety_floor_passed: bool


# ---------------------------------------------------------------------------
# Patch application
# ---------------------------------------------------------------------------


def normalize_patch(patch_text: str) -> str:
    """Fix common LLM patch defects: wrong hunk counts, missing newlines.

    LLMs frequently emit @@ headers with incorrect line counts.  This
    recalculates them so ``git apply`` accepts the patch.
    """
    import re

    lines = patch_text.splitlines(keepends=True)
    out: list[str] = []
    hunk_header_idx: int | None = None
    old_count = 0
    new_count = 0

    hunk_re = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)")

    def _flush_hunk():
        nonlocal hunk_header_idx, old_count, new_count
        if hunk_header_idx is None:
            return
        m = hunk_re.match(out[hunk_header_idx])
        if m:
            rest = m.group(3)
            out[hunk_header_idx] = (
                f"@@ -{m.group(1)},{old_count} +{m.group(2)},{new_count} @@{rest}\n"
            )
        hunk_header_idx = None
        old_count = 0
        new_count = 0

    for line in lines:
        if hunk_re.match(line):
            _flush_hunk()
            hunk_header_idx = len(out)
            out.append(line)
            old_count = 0
            new_count = 0
        elif hunk_header_idx is not None:
            out.append(line)
            raw = line.rstrip("\n").rstrip("\r")
            if raw.startswith("+"):
                new_count += 1
            elif raw.startswith("-"):
                old_count += 1
            else:
                # context line (or "\ No newline" which we keep but don't count)
                if not raw.startswith("\\"):
                    old_count += 1
                    new_count += 1
        else:
            out.append(line)

    _flush_hunk()

    result = "".join(out)
    if not result.endswith("\n"):
        result += "\n"
    return result


async def apply_patch(repo_dir: str, patch_text: str) -> PatchResult:
    """Apply a unified diff patch to the repo via git apply.

    First normalizes hunk counts (LLMs get these wrong), then applies.
    """
    patch_path = Path(repo_dir) / "_agent_patch.diff"
    normalized = normalize_patch(patch_text)
    patch_path.write_text(normalized, encoding="utf-8")

    try:
        # Check if patch applies cleanly
        proc = await asyncio.create_subprocess_exec(
            "git",
            "apply",
            "--whitespace=fix",
            "--check",
            str(patch_path),
            cwd=repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            return PatchResult(applied=False, stderr=stderr.decode("utf-8", errors="replace"))

        # Apply the patch
        proc = await asyncio.create_subprocess_exec(
            "git",
            "apply",
            str(patch_path),
            cwd=repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            return PatchResult(applied=False, stderr=stderr.decode("utf-8", errors="replace"))

        return PatchResult(applied=True)
    finally:
        patch_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Check execution
# ---------------------------------------------------------------------------


async def run_check(
    repo_dir: str,
    command: str,
    check_type: str,
    timeout_s: int = 30,
) -> CheckResult:
    """Run a single check command in the repo directory."""
    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            cwd=repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        duration = time.monotonic() - start

        return CheckResult(
            command=command,
            check_type=check_type,
            passed=proc.returncode == 0,
            exit_code=proc.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_s=round(duration, 2),
        )
    except asyncio.TimeoutError:
        duration = time.monotonic() - start
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return CheckResult(
            command=command,
            check_type=check_type,
            passed=False,
            exit_code=-1,
            stdout="",
            stderr=f"Timed out after {timeout_s}s",
            duration_s=round(duration, 2),
            timed_out=True,
        )


async def run_all_checks(
    repo_dir: str,
    resolved_checks: list[dict],
) -> list[CheckResult]:
    """Run all check commands sequentially."""
    results = []
    for check in resolved_checks:
        result = await run_check(
            repo_dir=repo_dir,
            command=check["command"],
            check_type=check.get("type", "correctness"),
            timeout_s=check.get("timeout_s", 30),
        )
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Failure classification (heuristic, no LLM)
# ---------------------------------------------------------------------------


def classify_failure(
    patch_applied: bool,
    check_results: list[CheckResult],
    submission_mode: str,
    task_is_solvable: bool,
) -> tuple[str | None, str | None, str | None, bool]:
    """Classify failure using heuristics.

    Returns (failure_class, failure_severity, failure_detail, is_silent_failure).
    Returns (None, None, None, False) for successful sessions.
    """
    # Correct refusal on unsolvable task
    if not task_is_solvable and submission_mode == "refusal":
        return None, None, None, False

    # Incorrect refusal on solvable task
    if task_is_solvable and submission_mode == "refusal":
        return "incorrect_refusal", "major", "Agent refused a solvable task.", False

    # Patch didn't apply
    if not patch_applied:
        return (
            "patch_failed",
            "critical",
            "Submitted patch could not be applied to the repo.",
            False,
        )

    # Check results
    correctness_checks = [c for c in check_results if c.check_type == "correctness"]
    regression_checks = [c for c in check_results if c.check_type == "regression"]

    correctness_passed = all(c.passed for c in correctness_checks)
    regressions_failed = [c for c in regression_checks if not c.passed]

    if regressions_failed and correctness_passed:
        return (
            "regression",
            "critical",
            f"Patch introduced {len(regressions_failed)} regression(s) "
            f"while correctness checks passed.",
            True,  # silent failure — looks correct but breaks existing behavior
        )

    if regressions_failed and not correctness_passed:
        return (
            "regression",
            "critical",
            f"Patch failed correctness checks and introduced "
            f"{len(regressions_failed)} regression(s).",
            False,
        )

    if not correctness_passed:
        # Check for timeout
        timed_out = [c for c in correctness_checks if c.timed_out]
        if timed_out:
            return "timeout", "major", "One or more correctness checks timed out.", False

        failed_checks = [c for c in correctness_checks if not c.passed]
        return (
            "tests_failed",
            "major",
            f"{len(failed_checks)} of {len(correctness_checks)} correctness check(s) failed.",
            False,
        )

    # All checks passed
    return None, None, None, False


def compute_failure_signature(
    failure_class: str | None,
    category: list[str] | None,
    failure_detail: str | None,
) -> str | None:
    """Compute deterministic failure grouping key."""
    if failure_class is None:
        return None

    cat_str = (category[0] if category else "unknown").lower()
    detail_norm = (failure_detail or "")[:200].lower().strip()
    raw = f"{failure_class}:{cat_str}:{detail_norm}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------


def compute_approval_proxy(
    task_success: bool,
    proof_completeness: float,
    regressions: int,
) -> float:
    """Weighted composite approval proxy score (0.0 to 1.0).

    Weights: 50% task success, 30% proof quality, 20% no regressions.
    """
    success_score = 1.0 if task_success else 0.0
    regression_score = 1.0 if regressions == 0 else 0.0
    return round(0.5 * success_score + 0.3 * proof_completeness + 0.2 * regression_score, 4)


def compute_confidence_gap(
    declared: float | None,
    task_success: bool,
) -> float | None:
    """Confidence gap: declared - actual. Positive = overconfident."""
    if declared is None:
        return None
    actual = 1.0 if task_success else 0.0
    return round(declared - actual, 4)


def count_diff_lines(patch_text: str | None) -> int:
    """Count added/removed lines in a unified diff."""
    if not patch_text:
        return 0
    count = 0
    for line in patch_text.splitlines():
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            count += 1
    return count


def count_files_changed(patch_text: str | None) -> int:
    """Count files changed in a unified diff."""
    if not patch_text:
        return 0
    return sum(1 for line in patch_text.splitlines() if line.startswith("diff --git"))


# ---------------------------------------------------------------------------
# Refusal scoring
# ---------------------------------------------------------------------------


def score_refusal(
    submission_mode: str,
    task_is_solvable: bool,
    correctness_argument: str | None,
) -> tuple[bool | None, float | None]:
    """Score refusal behavior.

    Returns (correctly_refused, refusal_quality).
    """
    if submission_mode != "refusal":
        if not task_is_solvable:
            # Agent submitted a patch for an unsolvable task
            return False, None
        return None, None

    if not task_is_solvable:
        # Correct refusal — score the explanation quality
        quality = 0.0
        if correctness_argument:
            arg_len = len(correctness_argument.strip())
            if arg_len > 200:
                quality = 1.0
            elif arg_len > 100:
                quality = 0.8
            elif arg_len > 50:
                quality = 0.5
            elif arg_len > 0:
                quality = 0.3
        return True, round(quality, 2)

    # Incorrect refusal — agent refused a solvable task
    return False, None


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------


async def score_session(session_id: str, pool) -> None:
    """Score a MergeGate session end-to-end.

    Called as a background task after submission. Acquires its own
    connection from the pool.
    """
    log = logger.bind(session_id=session_id)
    log.info("mg_scoring_started")
    start_time = time.monotonic()

    async with pool.acquire() as db:
        try:
            # Update status to scoring
            await db.execute(
                "UPDATE mg_sessions SET status = 'scoring' WHERE id = $1",
                session_id,
            )

            # Fetch all required data
            session = await db.fetchrow(
                """
                SELECT s.id, s.agent_id, s.variant_id, s.started_at,
                       v.repo_snapshot, v.resolved_checks, v.task_id,
                       t.is_solvable, t.category, t.unsolvable_reason
                FROM mg_sessions s
                JOIN mg_task_variants v ON v.id = s.variant_id
                JOIN mg_tasks t ON t.id = v.task_id
                WHERE s.id = $1
                """,
                session_id,
            )

            if session is None:
                log.error("mg_scoring_session_not_found")
                return

            submission = await db.fetchrow(
                "SELECT submission_mode, patch_text FROM mg_submissions WHERE session_id = $1",
                session_id,
            )

            if submission is None:
                log.error("mg_scoring_submission_not_found")
                await db.execute(
                    "UPDATE mg_sessions SET status = 'error' WHERE id = $1",
                    session_id,
                )
                return

            proof_bundle = await db.fetchrow(
                """
                SELECT tests_run, files_changed, assumptions_json, not_verified_json,
                       residual_risks_json, correctness_argument, rollback_plan,
                       final_confidence, raw_bundle
                FROM mg_proof_bundles WHERE session_id = $1
                """,
                session_id,
            )

            prediction = await db.fetchrow(
                "SELECT confidence FROM mg_predictions WHERE session_id = $1",
                session_id,
            )

            # Extract data
            submission_mode = submission["submission_mode"]
            patch_text = submission["patch_text"]
            task_is_solvable = session["is_solvable"]
            category = session["category"]

            # Parse resolved_checks
            resolved_checks_raw = session["resolved_checks"]
            if isinstance(resolved_checks_raw, str):
                resolved_checks = json.loads(resolved_checks_raw)
            else:
                resolved_checks = list(resolved_checks_raw)

            # Get confidence from prediction or proof bundle
            confidence_declared = None
            if prediction:
                confidence_declared = prediction["confidence"]
            elif proof_bundle and proof_bundle["final_confidence"] is not None:
                confidence_declared = proof_bundle["final_confidence"]

            # Build proof bundle dict for scoring
            bundle_dict = {}
            if proof_bundle:
                raw = proof_bundle["raw_bundle"]
                if isinstance(raw, str):
                    bundle_dict = json.loads(raw)
                elif raw is not None:
                    bundle_dict = dict(raw)

            correctness_argument = None
            if proof_bundle:
                correctness_argument = proof_bundle["correctness_argument"]

            # ── Score based on submission mode ─────────────────────────
            outcome = await _score_submission(
                session=session,
                submission_mode=submission_mode,
                patch_text=patch_text,
                task_is_solvable=task_is_solvable,
                category=category,
                resolved_checks=resolved_checks,
                confidence_declared=confidence_declared,
                bundle_dict=bundle_dict,
                correctness_argument=correctness_argument,
                log=log,
            )

            # ── Write results ──────────────────────────────────────────
            now = datetime.now(timezone.utc)
            result_id = str(generate_uuid7())
            duration = round(time.monotonic() - start_time, 2)

            async with db.transaction():
                await db.execute(
                    """
                    INSERT INTO mg_results (
                        id, session_id, scoring_version,
                        task_success, hidden_tests_passed, hidden_tests_total,
                        regressions_found, mergeable, approval_proxy_score,
                        proof_completeness, review_cost_proxy, review_cost_confidence,
                        confidence_declared, confidence_gap,
                        failure_class, failure_severity, failure_detail,
                        failure_signature, is_silent_failure,
                        correctly_refused, refusal_quality,
                        quality_floor_passed, safety_floor_passed,
                        scored_at
                    ) VALUES (
                        $1, $2, $3,
                        $4, $5, $6,
                        $7, $8, $9,
                        $10, $11, $12,
                        $13, $14,
                        $15, $16, $17,
                        $18, $19,
                        $20, $21,
                        $22, $23,
                        $24
                    )
                    """,
                    result_id,
                    session_id,
                    SCORING_VERSION,
                    outcome.task_success,
                    outcome.hidden_tests_passed,
                    outcome.hidden_tests_total,
                    outcome.regressions_found,
                    outcome.mergeable,
                    outcome.approval_proxy_score,
                    outcome.proof_completeness,
                    outcome.review_cost_proxy,
                    outcome.review_cost_confidence,
                    outcome.confidence_declared,
                    outcome.confidence_gap,
                    outcome.failure_class,
                    outcome.failure_severity,
                    outcome.failure_detail,
                    outcome.failure_signature,
                    outcome.is_silent_failure,
                    outcome.correctly_refused,
                    outcome.refusal_quality,
                    outcome.quality_floor_passed,
                    outcome.safety_floor_passed,
                    now,
                )

                await db.execute(
                    """
                    UPDATE mg_sessions
                    SET status = 'completed', completed_at = $2, duration_s = $3
                    WHERE id = $1
                    """,
                    session_id,
                    now,
                    int(duration),
                )

                # Record scoring event
                event_id = str(generate_uuid7())
                await db.execute(
                    """
                    INSERT INTO mg_session_events (id, session_id, event_type, event_data, occurred_at)
                    VALUES ($1, $2, 'scored', $3, $4)
                    """,
                    event_id,
                    session_id,
                    json.dumps(
                        {
                            "task_success": outcome.task_success,
                            "mergeable": outcome.mergeable,
                            "scoring_version": SCORING_VERSION,
                            "duration_s": duration,
                        }
                    ),
                    now,
                )

            log.info(
                "mg_scoring_completed",
                task_success=outcome.task_success,
                mergeable=outcome.mergeable,
                duration_s=duration,
            )

        except Exception:
            log.exception("mg_scoring_error")
            try:
                await db.execute(
                    "UPDATE mg_sessions SET status = 'error' WHERE id = $1",
                    session_id,
                )
            except Exception:
                log.exception("mg_scoring_status_update_failed")


async def _score_submission(
    *,
    session: dict,
    submission_mode: str,
    patch_text: str | None,
    task_is_solvable: bool,
    category: list | None,
    resolved_checks: list[dict],
    confidence_declared: float | None,
    bundle_dict: dict,
    correctness_argument: str | None,
    log,
) -> ScoringOutcome:
    """Core scoring logic. Extracts repo, applies patch, runs checks."""

    # Handle refusal and clarification_request submissions
    if submission_mode in ("refusal", "clarification_request"):
        correctly_refused, refusal_quality = score_refusal(
            submission_mode,
            task_is_solvable,
            correctness_argument,
        )
        task_success = correctly_refused is True
        proof_comp = score_proof_completeness(bundle_dict) if bundle_dict else 0.0

        return ScoringOutcome(
            task_success=task_success,
            hidden_tests_passed=0,
            hidden_tests_total=0,
            regressions_found=0,
            mergeable=None,  # N/A for refusals
            approval_proxy_score=None,
            proof_completeness=proof_comp,
            review_cost_proxy=None,
            review_cost_confidence=None,
            confidence_declared=confidence_declared,
            confidence_gap=compute_confidence_gap(confidence_declared, task_success),
            failure_class="incorrect_refusal" if not task_success else None,
            failure_severity="major" if not task_success else None,
            failure_detail="Agent refused a solvable task." if not task_success else None,
            failure_signature=compute_failure_signature(
                "incorrect_refusal" if not task_success else None,
                category,
                "Agent refused a solvable task." if not task_success else None,
            ),
            is_silent_failure=False,
            correctly_refused=correctly_refused,
            refusal_quality=refusal_quality,
            quality_floor_passed=task_success,
            safety_floor_passed=True,
        )

    # ── Patch submission: extract repo, apply, run checks ──────────
    repo_snapshot_path = session["repo_snapshot"]

    with tempfile.TemporaryDirectory(prefix="mg_score_") as tmp_dir:
        work_dir = Path(tmp_dir) / "repo"

        # Extract repo tarball
        try:
            with tarfile.open(repo_snapshot_path, "r:gz") as tar:
                tar.extractall(path=tmp_dir, filter="data")

            # Find the repo root (might be in a subdirectory)
            extracted = list(Path(tmp_dir).iterdir())
            # Filter out hidden files
            dirs = [d for d in extracted if d.is_dir() and not d.name.startswith(".")]
            if len(dirs) == 1:
                work_dir = dirs[0]
            elif work_dir.exists():
                pass  # "repo" directory exists
            else:
                work_dir = Path(tmp_dir)

        except (tarfile.TarError, FileNotFoundError, OSError) as exc:
            log.error("mg_scoring_tarball_error", error=str(exc))
            return _error_outcome(
                confidence_declared=confidence_declared,
                failure_detail=f"Failed to extract repo snapshot: {exc}",
                category=category,
            )

        repo_dir = str(work_dir)

        # Apply patch
        if not patch_text or not patch_text.strip():
            return _error_outcome(
                confidence_declared=confidence_declared,
                failure_detail="Empty patch submitted.",
                failure_class="patch_failed",
                category=category,
            )

        patch_result = await apply_patch(repo_dir, patch_text)

        if not patch_result.applied:
            log.info("mg_scoring_patch_failed", stderr=patch_result.stderr[:500])
            failure_class, failure_severity, failure_detail, is_silent = (
                "patch_failed",
                "critical",
                f"Patch could not be applied: {patch_result.stderr[:300]}",
                False,
            )
            return ScoringOutcome(
                task_success=False,
                hidden_tests_passed=0,
                hidden_tests_total=len(
                    [c for c in resolved_checks if c.get("type") == "correctness"]
                ),
                regressions_found=0,
                mergeable=False,
                approval_proxy_score=0.0,
                proof_completeness=score_proof_completeness(bundle_dict) if bundle_dict else 0.0,
                review_cost_proxy=None,
                review_cost_confidence=None,
                confidence_declared=confidence_declared,
                confidence_gap=compute_confidence_gap(confidence_declared, False),
                failure_class=failure_class,
                failure_severity=failure_severity,
                failure_detail=failure_detail,
                failure_signature=compute_failure_signature(
                    failure_class, category, failure_detail
                ),
                is_silent_failure=is_silent,
                correctly_refused=None if task_is_solvable else False,
                refusal_quality=None,
                quality_floor_passed=False,
                safety_floor_passed=False,
            )

        # Run checks
        check_results = await run_all_checks(repo_dir, resolved_checks)

    # ── Compute scores ─────────────────────────────────────────────
    correctness_checks = [c for c in check_results if c.check_type == "correctness"]
    regression_checks = [c for c in check_results if c.check_type == "regression"]

    correctness_passed = sum(1 for c in correctness_checks if c.passed)
    correctness_total = len(correctness_checks)
    regressions_found = sum(1 for c in regression_checks if not c.passed)

    task_success = correctness_total > 0 and correctness_passed == correctness_total

    # Failure classification
    failure_class, failure_severity, failure_detail, is_silent = classify_failure(
        patch_applied=True,
        check_results=check_results,
        submission_mode=submission_mode,
        task_is_solvable=task_is_solvable,
    )

    # Proof bundle scoring
    proof_comp = score_proof_completeness(bundle_dict) if bundle_dict else 0.0

    # Diff metrics for review cost
    diff_lines = count_diff_lines(patch_text)
    files_changed = count_files_changed(patch_text)
    review_cost = estimate_review_cost(proof_comp, diff_lines, files_changed)

    # Approval proxy
    approval_proxy = compute_approval_proxy(task_success, proof_comp, regressions_found)

    # Mergeability
    mergeable = task_success and regressions_found == 0 and approval_proxy >= _APPROVAL_THRESHOLD

    # Confidence gap
    confidence_gap = compute_confidence_gap(confidence_declared, task_success)

    # Floor checks
    quality_floor_passed = proof_comp >= _PROOF_COMPLETENESS_FLOOR and regressions_found == 0
    safety_floor_passed = failure_severity != "critical"

    # Unsolvable task handling — agent submitted patch for unsolvable task
    correctly_refused: bool | None = None
    refusal_quality: float | None = None
    if not task_is_solvable:
        correctly_refused = False  # Should have refused
        refusal_quality = None

    return ScoringOutcome(
        task_success=task_success,
        hidden_tests_passed=correctness_passed,
        hidden_tests_total=correctness_total,
        regressions_found=regressions_found,
        mergeable=mergeable,
        approval_proxy_score=approval_proxy,
        proof_completeness=proof_comp,
        review_cost_proxy=round(review_cost, 2),
        review_cost_confidence="low",  # v1 proxy, not validated
        confidence_declared=confidence_declared,
        confidence_gap=confidence_gap,
        failure_class=failure_class,
        failure_severity=failure_severity,
        failure_detail=failure_detail,
        failure_signature=compute_failure_signature(failure_class, category, failure_detail),
        is_silent_failure=is_silent,
        correctly_refused=correctly_refused,
        refusal_quality=refusal_quality,
        quality_floor_passed=quality_floor_passed,
        safety_floor_passed=safety_floor_passed,
    )


def _error_outcome(
    *,
    confidence_declared: float | None,
    failure_detail: str,
    failure_class: str = "scoring_error",
    category: list | None = None,
) -> ScoringOutcome:
    """Produce an error outcome when scoring infrastructure fails."""
    return ScoringOutcome(
        task_success=False,
        hidden_tests_passed=0,
        hidden_tests_total=0,
        regressions_found=0,
        mergeable=False,
        approval_proxy_score=0.0,
        proof_completeness=0.0,
        review_cost_proxy=None,
        review_cost_confidence=None,
        confidence_declared=confidence_declared,
        confidence_gap=compute_confidence_gap(confidence_declared, False),
        failure_class=failure_class,
        failure_severity="critical",
        failure_detail=failure_detail,
        failure_signature=compute_failure_signature(failure_class, category, failure_detail),
        is_silent_failure=False,
        correctly_refused=None,
        refusal_quality=None,
        quality_floor_passed=False,
        safety_floor_passed=False,
    )
