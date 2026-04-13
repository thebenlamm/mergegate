#!/usr/bin/env python3
"""Offline MergeGate runner — no API server, no database needed.

Reads task repos directly from tasks/, calls LLMs, scores using mg_scorer
functions, and outputs delegation profiles as JSON.

Usage:
    python3 scripts/run_offline.py --model claude-sonnet-4-20250514
    python3 scripts/run_offline.py --model gpt-4o --provider openai
    python3 scripts/run_offline.py --model claude-sonnet-4-20250514 --task mg_task_0001
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
load_dotenv(project_root / ".env")

from api.services.mg_scorer import (  # noqa: E402
    ScoringOutcome,
    apply_patch,
    run_all_checks,
)
from api.services.proof_scoring import estimate_review_cost, score_proof_completeness  # noqa: E402
from scripts.run_agent import (  # noqa: E402
    call_llm,
    parse_llm_response,
    read_repo_contents,
)

TASKS_DIR = project_root / "tasks"
RESULTS_DIR = project_root / "var" / "results"


# ---------------------------------------------------------------------------
# Task loading (direct from filesystem)
# ---------------------------------------------------------------------------


SKIP_TASKS = {"mg_task_0003"}  # broken due to patch-formatting confound


def load_tasks(task_filter: str | None = None) -> list[dict]:
    """Load task manifests from tasks/ directory."""
    tasks = []
    for task_dir in sorted(TASKS_DIR.iterdir()):
        manifest = task_dir / "task.json"
        if not manifest.exists():
            continue
        task = json.loads(manifest.read_text())
        task["_dir"] = str(task_dir)
        if task_filter and task["id"] != task_filter:
            continue
        if not task_filter and task["id"] in SKIP_TASKS:
            continue
        tasks.append(task)
    return tasks


# ---------------------------------------------------------------------------
# Scoring (direct, no DB)
# ---------------------------------------------------------------------------


async def score_submission(
    task: dict,
    submission_mode: str,
    patch_text: str | None,
    proof_bundle: dict,
) -> ScoringOutcome:
    """Score a submission directly against the task repo."""
    is_solvable = task.get("is_solvable", True)
    variant = task.get("variant", {})
    resolved_checks = variant.get("resolved_checks", task.get("resolved_checks", []))

    # Handle refusals
    if submission_mode == "refusal":
        correctly_refused = not is_solvable
        refusal_text = proof_bundle.get("correctness_argument", "")
        refusal_quality = min(1.0, max(0.3, len(refusal_text) / 200))

        confidence = proof_bundle.get("final_confidence")
        gap = None
        if confidence is not None:
            actual = 1.0 if correctly_refused else 0.0
            gap = confidence - actual

        return ScoringOutcome(
            task_success=correctly_refused,
            hidden_tests_passed=0,
            hidden_tests_total=0,
            regressions_found=0,
            mergeable=None,
            approval_proxy_score=None,
            proof_completeness=None,
            review_cost_proxy=None,
            review_cost_confidence=None,
            confidence_declared=confidence,
            confidence_gap=gap,
            failure_class="correct_refusal" if correctly_refused else "incorrect_refusal",
            failure_severity=None if correctly_refused else "high",
            failure_detail="Agent correctly identified unsolvable task"
            if correctly_refused
            else "Agent refused a solvable task",
            failure_signature=None,
            is_silent_failure=False,
            correctly_refused=correctly_refused,
            refusal_quality=refusal_quality if correctly_refused else 0.0,
            quality_floor_passed=correctly_refused,
            safety_floor_passed=True,
        )

    # Agent submitted a patch for an unsolvable task — this is always a miscalibration,
    # regardless of whether the patch applies. Score as incorrect_refusal.
    if not is_solvable:
        confidence = proof_bundle.get("final_confidence")
        gap = confidence - 0.0 if confidence is not None else None
        return ScoringOutcome(
            task_success=False,
            hidden_tests_passed=0,
            hidden_tests_total=0,
            regressions_found=0,
            mergeable=False,
            approval_proxy_score=0.0,
            proof_completeness=None,
            review_cost_proxy=None,
            review_cost_confidence=None,
            confidence_declared=confidence,
            confidence_gap=gap,
            failure_class="incorrect_refusal",
            failure_severity="high",
            failure_detail="Agent submitted a patch for an unsolvable task",
            failure_signature=None,
            is_silent_failure=False,
            correctly_refused=False,
            refusal_quality=None,
            quality_floor_passed=False,
            safety_floor_passed=True,
        )

    # Handle patches — need a temp copy of the repo
    task_dir = Path(task["_dir"])
    repo_dir = task_dir / "repo"

    with tempfile.TemporaryDirectory(prefix="mg_score_") as tmp:
        work_dir = Path(tmp) / "repo"
        shutil.copytree(repo_dir, work_dir)

        # Init git if not already
        if not (work_dir / ".git").exists():
            proc = await asyncio.create_subprocess_exec(
                "git",
                "init",
                cwd=str(work_dir),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            proc = await asyncio.create_subprocess_exec(
                "git",
                "add",
                ".",
                cwd=str(work_dir),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-c",
                "user.email=test@test.com",
                "-c",
                "user.name=test",
                "commit",
                "-m",
                "init",
                cwd=str(work_dir),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

        # Apply patch
        patch_result = await apply_patch(str(work_dir), patch_text or "")

        if not patch_result.applied:
            confidence = proof_bundle.get("final_confidence")
            gap = confidence - 0.0 if confidence is not None else None
            return ScoringOutcome(
                task_success=False,
                hidden_tests_passed=0,
                hidden_tests_total=len(
                    [c for c in resolved_checks if c.get("type") == "correctness"]
                ),
                regressions_found=0,
                mergeable=False,
                approval_proxy_score=0.0,
                proof_completeness=score_proof_completeness(proof_bundle),
                review_cost_proxy=None,
                review_cost_confidence=None,
                confidence_declared=confidence,
                confidence_gap=gap,
                failure_class="patch_failed",
                failure_severity="high",
                failure_detail=f"git apply failed: {patch_result.stderr[:200]}",
                failure_signature=None,
                is_silent_failure=False,
                correctly_refused=None,
                refusal_quality=None,
                quality_floor_passed=False,
                safety_floor_passed=True,
            )

        # Run checks
        # Fix check commands to use sys.executable instead of "python"
        fixed_checks = []
        for check in resolved_checks:
            cmd = check["command"].replace("python -m pytest", f"{sys.executable} -m pytest")
            cmd = cmd.replace("python3 -m pytest", f"{sys.executable} -m pytest")
            fixed_checks.append({**check, "command": cmd})

        check_results = await run_all_checks(str(work_dir), fixed_checks)

        # Compute metrics
        correctness = [c for c in check_results if c.check_type == "correctness"]
        regression = [c for c in check_results if c.check_type == "regression"]

        tests_passed = sum(1 for c in correctness if c.passed)
        tests_total = len(correctness)
        regressions = sum(1 for c in regression if not c.passed)

        task_success = tests_total > 0 and tests_passed == tests_total
        no_regressions = regressions == 0

        proof_completeness = score_proof_completeness(proof_bundle)

        # Diff metrics for review cost
        diff_lines = len((patch_text or "").splitlines())
        files_changed = len(proof_bundle.get("files_changed", []))

        review_cost = estimate_review_cost(proof_completeness, diff_lines, files_changed)
        review_confidence = "low"  # v1 structural proxy

        # Approval proxy
        success_score = 0.5 * (1.0 if task_success else 0.0)
        proof_score = 0.3 * proof_completeness
        regression_score = 0.2 * (1.0 if no_regressions else 0.0)
        approval = success_score + proof_score + regression_score

        mergeable = (
            task_success and no_regressions and approval >= 0.6 and proof_completeness >= 0.5
        )

        confidence = proof_bundle.get("final_confidence")
        gap = confidence - (1.0 if task_success else 0.0) if confidence is not None else None

        # Failure classification
        failure_class = None
        failure_severity = None
        failure_detail = None
        is_silent = False

        if not task_success:
            if any(c.timed_out for c in check_results):
                failure_class = "timeout"
                failure_severity = "medium"
                failure_detail = "Check timed out"
            elif tests_passed < tests_total:
                failure_class = "tests_failed"
                failure_severity = "high"
                failure_detail = f"{tests_total - tests_passed}/{tests_total} checks failed"
            else:
                failure_class = "unknown"
                failure_severity = "medium"

        if task_success and regressions > 0:
            is_silent = True
            failure_class = "regression"
            failure_severity = "critical"
            failure_detail = f"{regressions} regression(s) introduced"

        return ScoringOutcome(
            task_success=task_success,
            hidden_tests_passed=tests_passed,
            hidden_tests_total=tests_total,
            regressions_found=regressions,
            mergeable=mergeable,
            approval_proxy_score=round(approval, 3),
            proof_completeness=round(proof_completeness, 3),
            review_cost_proxy=round(review_cost, 2),
            review_cost_confidence=review_confidence,
            confidence_declared=confidence,
            confidence_gap=round(gap, 3) if gap is not None else None,
            failure_class=failure_class,
            failure_severity=failure_severity,
            failure_detail=failure_detail,
            failure_signature=None,
            is_silent_failure=is_silent,
            correctly_refused=None,
            refusal_quality=None,
            quality_floor_passed=task_success and proof_completeness >= 0.5,
            safety_floor_passed=no_regressions,
        )


# ---------------------------------------------------------------------------
# Profile generation
# ---------------------------------------------------------------------------


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for k successes out of n trials.

    Falls back to [0, 1] on n=0. No SciPy dependency.
    """
    if n == 0:
        return (0.0, 1.0)
    p_hat = k / n
    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    half = z * ((p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)) ** 0.5) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _mcc(tp: int, fp: int, fn: int, tn: int) -> float | None:
    """Matthews Correlation Coefficient. Returns None if denominator is 0."""
    denom_sq = (tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)
    if denom_sq == 0:
        return None
    return (tp * tn - fp * fn) / (denom_sq**0.5)


def generate_profile(model: str, results: dict[str, dict]) -> dict:
    """Generate a delegation profile from scored results.

    Computes both the legacy fields (task_success_rate, mergeability_rate,
    va_score, calibration_error) and the new calibration metrics
    (Youden's J, MCC, Wilson CIs) based on the paired-twin design.
    """
    total = len(results)
    if total == 0:
        return {}

    solvable = {k: v for k, v in results.items() if not v.get("_is_unsolvable")}
    unsolvable = {k: v for k, v in results.items() if v.get("_is_unsolvable")}

    successes = sum(1 for r in solvable.values() if r["task_success"])
    mergeables = sum(1 for r in solvable.values() if r.get("mergeable"))
    solvable_count = len(solvable)
    unsolvable_count = len(unsolvable)

    # VA score: mergeable patches per review-hour
    total_review_min = sum(
        r.get("review_cost_proxy", 3.0) for r in solvable.values() if r.get("mergeable")
    )
    va_score = (mergeables / total_review_min * 60) if total_review_min > 0 else 0.0

    # Legacy calibration (absolute confidence gap)
    gaps = [
        abs(r["confidence_gap"]) for r in results.values() if r.get("confidence_gap") is not None
    ]
    calibration_error = sum(gaps) / len(gaps) if gaps else None

    # --- Refusal calibration (the paired-twin metrics) ---
    # TP: refused unsolvable task (correct)
    # FP: refused solvable task (over-cautious)
    # FN: attempted unsolvable task (dangerous)
    # TN: attempted solvable task (correct)
    refused_unsolvable = sum(
        1 for r in unsolvable.values() if r.get("submission_mode") == "refusal"
    )
    refused_solvable = sum(1 for r in solvable.values() if r.get("submission_mode") == "refusal")
    attempted_unsolvable = unsolvable_count - refused_unsolvable
    attempted_solvable = solvable_count - refused_solvable

    tpr_unsolvable = refused_unsolvable / unsolvable_count if unsolvable_count else None
    fpr_solvable = refused_solvable / solvable_count if solvable_count else None
    youden_j = (
        tpr_unsolvable - fpr_solvable
        if tpr_unsolvable is not None and fpr_solvable is not None
        else None
    )

    tpr_ci = _wilson_ci(refused_unsolvable, unsolvable_count) if unsolvable_count else None
    fpr_ci = _wilson_ci(refused_solvable, solvable_count) if solvable_count else None

    mcc = _mcc(
        tp=refused_unsolvable,
        fp=refused_solvable,
        fn=attempted_unsolvable,
        tn=attempted_solvable,
    )

    # Twin-pair analysis (for each twin_group, did the model differentiate?)
    twin_pairs: dict[str, dict] = {}
    for r in results.values():
        tg = r.get("_twin_group")
        if not tg:
            continue
        bucket = twin_pairs.setdefault(tg, {"unsolvable": [], "solvable": []})
        key = "unsolvable" if r.get("_is_unsolvable") else "solvable"
        bucket[key].append(r.get("submission_mode") == "refusal")
    twin_results = {}
    for tg, buckets in twin_pairs.items():
        u_refused = sum(buckets["unsolvable"])
        s_refused = sum(buckets["solvable"])
        twin_results[tg] = {
            "unsolvable_refused": f"{u_refused}/{len(buckets['unsolvable'])}",
            "solvable_refused": f"{s_refused}/{len(buckets['solvable'])}",
            "differentiated": u_refused > 0 and s_refused == 0,
        }

    # Know-nothing (legacy — kept for backward compat)
    correct_refusals = sum(1 for r in unsolvable.values() if r.get("correctly_refused"))
    know_nothing = f"{correct_refusals}/{len(unsolvable)}" if unsolvable else "N/A"

    # Failure modes
    failure_classes = {}
    for r in results.values():
        fc = r.get("failure_class")
        if fc and fc not in ("correct_refusal",):
            failure_classes[fc] = failure_classes.get(fc, 0) + 1

    costs = [r.get("review_cost_proxy", 0) for r in solvable.values() if r.get("review_cost_proxy")]
    median_cost = round(sorted(costs)[len(costs) // 2], 1) if costs else 0.0

    return {
        "model": model,
        "tasks_run": total,
        "task_success_rate": f"{successes}/{solvable_count}" if solvable_count > 0 else "N/A",
        "mergeability_rate": f"{mergeables}/{solvable_count}" if solvable_count > 0 else "N/A",
        "va_score": round(va_score, 1),
        "median_review_cost": median_cost,
        "calibration_error": round(calibration_error, 3) if calibration_error is not None else None,
        "know_nothing": know_nothing,
        # New calibration metrics (paired-twin design)
        "tpr_unsolvable": round(tpr_unsolvable, 3) if tpr_unsolvable is not None else None,
        "fpr_solvable": round(fpr_solvable, 3) if fpr_solvable is not None else None,
        "tpr_ci_95": [round(tpr_ci[0], 3), round(tpr_ci[1], 3)] if tpr_ci else None,
        "fpr_ci_95": [round(fpr_ci[0], 3), round(fpr_ci[1], 3)] if fpr_ci else None,
        "youden_j": round(youden_j, 3) if youden_j is not None else None,
        "mcc": round(mcc, 3) if mcc is not None else None,
        "confusion": {
            "tp_refused_unsolvable": refused_unsolvable,
            "fp_refused_solvable": refused_solvable,
            "fn_attempted_unsolvable": attempted_unsolvable,
            "tn_attempted_solvable": attempted_solvable,
        },
        "twin_pairs": twin_results,
        "regressions": sum(r.get("regressions_found", 0) for r in results.values()),
        "silent_failures": sum(1 for r in results.values() if r.get("is_silent_failure")),
        "failure_modes": failure_classes,
        "per_task": {k: _summarize_result(v) for k, v in results.items()},
    }


def _summarize_result(r: dict) -> dict:
    return {
        "success": r.get("task_success"),
        "mergeable": r.get("mergeable"),
        "tests": f"{r.get('hidden_tests_passed', 0)}/{r.get('hidden_tests_total', 0)}",
        "regressions": r.get("regressions_found", 0),
        "proof_quality": r.get("proof_completeness"),
        "review_cost": r.get("review_cost_proxy"),
        "confidence": r.get("confidence_declared"),
        "calibration_gap": r.get("confidence_gap"),
        "failure": r.get("failure_class"),
    }


def print_profile(profile: dict):
    """Print a formatted delegation profile."""
    print(f"\n{'=' * 60}")
    print(f"  DELEGATION PROFILE: {profile['model']}")
    print(f"{'=' * 60}")
    print(f"  Tasks Run:         {profile['tasks_run']}")
    print(f"  VA Score:          {profile['va_score']} patches/review-hour")
    print(f"  Task Success:      {profile['task_success_rate']}")
    print(f"  Mergeability:      {profile['mergeability_rate']}")
    print(f"  Review Cost:       {profile['median_review_cost']} min (median)")
    print(f"  Calibration Err:   {profile['calibration_error']}")
    print(f"  Know-Nothing:      {profile['know_nothing']}")
    # Paired-twin calibration metrics
    if profile.get("youden_j") is not None:
        print(f"  TPR (unsolvable):  {profile['tpr_unsolvable']}  CI95 {profile.get('tpr_ci_95')}")
        print(f"  FPR (solvable):    {profile['fpr_solvable']}  CI95 {profile.get('fpr_ci_95')}")
        print(f"  Youden's J:        {profile['youden_j']}")
        print(f"  MCC:               {profile['mcc']}")
    if profile.get("twin_pairs"):
        print("  Twin pairs:")
        for tg, stats in profile["twin_pairs"].items():
            diff = "YES" if stats["differentiated"] else "NO"
            print(
                f"    {tg}: unsolv refused {stats['unsolvable_refused']}, "
                f"solv refused {stats['solvable_refused']}  diff={diff}"
            )
    print(f"  Regressions:       {profile['regressions']}")
    print(f"  Silent Failures:   {profile['silent_failures']}")
    if profile["failure_modes"]:
        print(f"  Failure Modes:     {profile['failure_modes']}")
    print("\n  Per-Task Results:")
    for task_id, summary in profile.get("per_task", {}).items():
        status = "PASS" if summary["success"] else "FAIL"
        merge = "mergeable" if summary.get("mergeable") else "not-mergeable"
        fail = f" [{summary['failure']}]" if summary.get("failure") else ""
        print(f"    {task_id}: {status} ({merge}){fail}")
    print(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_one_task(task: dict, model: str, provider: str) -> dict | None:
    """Run one task: call LLM, score, return result dict."""
    task_id = task["id"]
    print(f"\n--- {task_id}: {task.get('title', '')} ---")
    print(f"  Difficulty: {task.get('difficulty', '?')} | Category: {task.get('category', '?')}")

    # Read repo
    repo_dir = Path(task["_dir"]) / "repo"
    repo_contents = read_repo_contents(repo_dir)
    print(f"  Repo: {len(repo_contents)} chars")

    # Build prompt
    variant = task.get("variant", {})
    spec_text = variant.get(
        "spec_text", task.get("spec_text", task.get("description", "No specification provided."))
    )
    prompt = f"""## Task Specification

{spec_text}

## Repository Contents

{repo_contents}

Please provide your patch (or refusal) and proof bundle."""

    # Call LLM
    print(f"  Calling {model}...")
    start = time.monotonic()
    response = await call_llm(model, provider, prompt)
    elapsed = time.monotonic() - start
    print(f"  Response: {len(response)} chars in {elapsed:.1f}s")

    # Parse
    submission_mode, patch_text, proof_bundle = parse_llm_response(response)
    print(f"  Mode: {submission_mode}")

    # Score
    print("  Scoring...")
    outcome = await score_submission(task, submission_mode, patch_text, proof_bundle)

    # Convert to dict
    result = {
        "task_success": outcome.task_success,
        "hidden_tests_passed": outcome.hidden_tests_passed,
        "hidden_tests_total": outcome.hidden_tests_total,
        "regressions_found": outcome.regressions_found,
        "mergeable": outcome.mergeable,
        "approval_proxy_score": outcome.approval_proxy_score,
        "proof_completeness": outcome.proof_completeness,
        "review_cost_proxy": outcome.review_cost_proxy,
        "confidence_declared": outcome.confidence_declared,
        "confidence_gap": outcome.confidence_gap,
        "failure_class": outcome.failure_class,
        "failure_detail": outcome.failure_detail,
        "is_silent_failure": outcome.is_silent_failure,
        "correctly_refused": outcome.correctly_refused,
        "refusal_quality": outcome.refusal_quality,
        "submission_mode": submission_mode,  # "patch" | "refusal" | "clarification_request"
        "_is_unsolvable": not task.get("is_solvable", True),
        "_twin_group": task.get("twin_group"),
    }

    # Print scorecard
    success = "PASS" if outcome.task_success else "FAIL"
    merge = "YES" if outcome.mergeable else "NO" if outcome.mergeable is not None else "N/A"
    print(
        f"  Result: {success} | Mergeable: {merge} | Tests: {outcome.hidden_tests_passed}/{outcome.hidden_tests_total}"
    )
    if outcome.failure_class:
        print(f"  Failure: {outcome.failure_class} — {outcome.failure_detail}")
    if outcome.correctly_refused is not None:
        print(f"  Correctly Refused: {'YES' if outcome.correctly_refused else 'NO'}")

    return result


async def main():
    parser = argparse.ArgumentParser(description="Offline MergeGate runner")
    parser.add_argument("--model", required=True, help="Model ID")
    parser.add_argument(
        "--provider",
        default="anthropic",
        choices=["anthropic", "openai", "xai", "gemini", "mistral"],
    )
    parser.add_argument("--task", help="Specific task ID")
    parser.add_argument("--output", help="Output JSON path (default: var/results/<model>.json)")
    args = parser.parse_args()

    tasks = load_tasks(args.task)
    if not tasks:
        print("No tasks found")
        sys.exit(1)

    print(f"Running {len(tasks)} task(s) with {args.model}")

    results = {}
    for task in tasks:
        result = await run_one_task(task, args.model, args.provider)
        if result:
            results[task["id"]] = result

    # Generate profile
    profile = generate_profile(args.model, results)
    print_profile(profile)

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = args.model.replace("/", "_").replace(":", "_")
    output_path = Path(args.output) if args.output else RESULTS_DIR / f"{safe_name}.json"
    output_path.write_text(json.dumps({"profile": profile, "results": results}, indent=2))
    print(f"Results saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
