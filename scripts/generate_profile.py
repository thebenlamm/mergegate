#!/usr/bin/env python3
"""Generate MergeGate delegation profiles from scored sessions.

Aggregates mg_results rows per agent into delegation profiles showing
mergeability, review cost, calibration, and failure modes.

Usage:
    python scripts/generate_profile.py --compare
    python scripts/generate_profile.py --agent-name DeepSolve
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass
class DelegationProfile:
    agent_name: str
    model: str
    total_sessions: int = 0
    # Task outcomes
    task_successes: int = 0
    mergeables: int = 0
    # Solvable-task metrics
    solvable_sessions: int = 0
    solvable_successes: int = 0
    solvable_mergeables: int = 0
    # Review cost
    review_costs: list[float] = field(default_factory=list)
    # Proof quality
    proof_scores: list[float] = field(default_factory=list)
    # Calibration
    confidence_gaps: list[float] = field(default_factory=list)
    # Refusals (unsolvable tasks)
    unsolvable_sessions: int = 0
    correct_refusals: int = 0
    # Failures
    failure_classes: dict[str, int] = field(default_factory=dict)
    silent_failures: int = 0
    regressions: int = 0

    @property
    def task_success_rate(self) -> float:
        if self.total_sessions == 0:
            return 0.0
        return self.task_successes / self.total_sessions

    @property
    def mergeability_rate(self) -> float:
        if self.solvable_sessions == 0:
            return 0.0
        return self.solvable_mergeables / self.solvable_sessions

    @property
    def va_score(self) -> float:
        """Verified Autonomy: mergeable patches per review-hour."""
        if not self.review_costs:
            return 0.0
        total_review_minutes = sum(self.review_costs)
        if total_review_minutes == 0:
            return 0.0
        return (self.solvable_mergeables / total_review_minutes) * 60

    @property
    def median_review_cost(self) -> float:
        if not self.review_costs:
            return 0.0
        s = sorted(self.review_costs)
        n = len(s)
        if n % 2 == 0:
            return (s[n // 2 - 1] + s[n // 2]) / 2
        return s[n // 2]

    @property
    def mean_proof_completeness(self) -> float:
        if not self.proof_scores:
            return 0.0
        return sum(self.proof_scores) / len(self.proof_scores)

    @property
    def calibration_error(self) -> float:
        """Mean absolute confidence gap. Lower = better calibrated."""
        if not self.confidence_gaps:
            return 0.0
        return sum(abs(g) for g in self.confidence_gaps) / len(self.confidence_gaps)

    @property
    def overconfidence_rate(self) -> float:
        if not self.confidence_gaps:
            return 0.0
        over = sum(1 for g in self.confidence_gaps if g > 0.2)
        return over / len(self.confidence_gaps)

    @property
    def know_nothing_score(self) -> float:
        """Correct refusal rate on unsolvable tasks."""
        if self.unsolvable_sessions == 0:
            return 0.0
        return self.correct_refusals / self.unsolvable_sessions

    @property
    def primary_failure_mode(self) -> str:
        if not self.failure_classes:
            return "none"
        return max(self.failure_classes, key=self.failure_classes.get)


def build_profile(agent_name: str, model: str, rows: list[dict]) -> DelegationProfile:
    """Build a DelegationProfile from mg_results rows."""
    p = DelegationProfile(agent_name=agent_name, model=model)
    p.total_sessions = len(rows)

    for r in rows:
        if r["task_success"]:
            p.task_successes += 1

        # Determine if this was an unsolvable task (from task definition, not agent behavior)
        is_unsolvable = not r["is_solvable"]

        if is_unsolvable:
            p.unsolvable_sessions += 1
            if r["correctly_refused"]:
                p.correct_refusals += 1
        else:
            p.solvable_sessions += 1
            if r["task_success"]:
                p.solvable_successes += 1
            if r["mergeable"]:
                p.solvable_mergeables += 1

        if r["review_cost_proxy"] is not None:
            p.review_costs.append(r["review_cost_proxy"])

        if r["proof_completeness"] is not None:
            p.proof_scores.append(r["proof_completeness"])

        if r["confidence_gap"] is not None:
            p.confidence_gaps.append(r["confidence_gap"])

        if r["is_silent_failure"]:
            p.silent_failures += 1

        if r["regressions_found"] and r["regressions_found"] > 0:
            p.regressions += 1

        fc = r.get("failure_class")
        if fc:
            p.failure_classes[fc] = p.failure_classes.get(fc, 0) + 1

    return p


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_single_profile(p: DelegationProfile) -> str:
    """Format a single agent's delegation profile."""
    lines = [
        "",
        "=" * 52,
        "  MERGEGATE DELEGATION PROFILE",
        "=" * 52,
        f"  Agent:     {p.agent_name}",
        f"  Model:     {p.model}",
        f"  Sessions:  {p.total_sessions}",
        "-" * 52,
        "",
        "  VERIFIED AUTONOMY",
        f"    {p.va_score:.1f} accepted patches / review-hour",
        "",
        "  MERGEABILITY",
        f"    Task success:    {p.solvable_successes}/{p.solvable_sessions}"
        + (
            f" ({p.solvable_successes / p.solvable_sessions * 100:.0f}%)"
            if p.solvable_sessions
            else ""
        ),
        f"    Mergeable:       {p.solvable_mergeables}/{p.solvable_sessions}"
        + (f" ({p.mergeability_rate * 100:.0f}%)" if p.solvable_sessions else ""),
        f"    Regressions:     {p.regressions}",
        "",
        "  REVIEW COST",
        f"    Median:          {p.median_review_cost:.1f} min",
        f"    Proof quality:   {p.mean_proof_completeness:.2f}",
        "",
        "  CALIBRATION",
        f"    Error:           {p.calibration_error:.2f}",
        f"    Overconfident:   {p.overconfidence_rate * 100:.0f}%",
    ]

    if p.unsolvable_sessions > 0:
        lines.append(
            f"    Know-Nothing:    {p.correct_refusals}/{p.unsolvable_sessions}"
            + f" ({p.know_nothing_score * 100:.0f}%)"
        )

    if p.failure_classes:
        lines.append("")
        lines.append("  FAILURE PROFILE")
        for fc, count in sorted(p.failure_classes.items(), key=lambda x: -x[1]):
            pct = count / p.total_sessions * 100
            lines.append(f"    {fc:20s} {count:3d} ({pct:.0f}%)")
        if p.silent_failures > 0:
            lines.append(f"    Silent failures: {p.silent_failures}")

    lines.extend(["", "=" * 52, ""])
    return "\n".join(lines)


def format_comparison(profiles: list[DelegationProfile]) -> str:
    """Format side-by-side comparison of multiple agents."""
    if not profiles:
        return "No profiles to compare."

    # Column widths
    label_w = 20
    col_w = 18

    def _header():
        h = " " * label_w
        for p in profiles:
            name = p.agent_name[: col_w - 2]
            h += name.center(col_w)
        return h

    def _sep():
        s = " " * label_w
        for _ in profiles:
            s += ("\u2500" * (col_w - 2)).center(col_w)
        return s

    def _row(label: str, values: list[str]):
        r = label.ljust(label_w)
        for v in values:
            r += v.center(col_w)
        return r

    lines = [
        "",
        " " * 8 + "MERGEGATE DELEGATION PROFILES",
        " " * 8 + "=" * 29,
        "",
        _header(),
        _sep(),
    ]

    # Task success
    lines.append(
        _row(
            "Task Success",
            [
                f"{p.solvable_successes}/{p.solvable_sessions}"
                + (
                    f" ({p.solvable_successes / p.solvable_sessions * 100:.0f}%)"
                    if p.solvable_sessions
                    else ""
                )
                for p in profiles
            ],
        )
    )

    # Mergeable
    lines.append(
        _row(
            "Mergeable",
            [
                f"{p.solvable_mergeables}/{p.solvable_sessions}"
                + (f" ({p.mergeability_rate * 100:.0f}%)" if p.solvable_sessions else "")
                for p in profiles
            ],
        )
    )

    # VA Score
    lines.append(_row("VA Score", [f"{p.va_score:.1f}" for p in profiles]))

    # Review Cost
    lines.append(_row("Review Cost", [f"{p.median_review_cost:.1f} min" for p in profiles]))

    # Proof Quality
    lines.append(_row("Proof Quality", [f"{p.mean_proof_completeness:.2f}" for p in profiles]))

    # Calibration
    lines.append(_row("Calibration Err", [f"{p.calibration_error:.2f}" for p in profiles]))

    # Know-Nothing
    has_unsolvable = any(p.unsolvable_sessions > 0 for p in profiles)
    if has_unsolvable:
        lines.append(
            _row(
                "Know-Nothing",
                [
                    f"{p.correct_refusals}/{p.unsolvable_sessions}"
                    if p.unsolvable_sessions > 0
                    else "N/A"
                    for p in profiles
                ],
            )
        )

    # Regressions
    lines.append(_row("Regressions", [str(p.regressions) for p in profiles]))

    # Silent Failures
    lines.append(_row("Silent Failures", [str(p.silent_failures) for p in profiles]))

    # Primary failure mode
    lines.append(_row("Top Failure", [p.primary_failure_mode for p in profiles]))

    lines.extend(["", _sep(), ""])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------


async def fetch_profiles(db) -> list[DelegationProfile]:
    """Fetch all agents with scored MergeGate sessions and build profiles."""
    rows = await db.fetch(
        """
        SELECT
            a.agent_name, a.model,
            r.task_success, r.hidden_tests_passed, r.hidden_tests_total,
            r.regressions_found, r.mergeable, r.approval_proxy_score,
            r.proof_completeness, r.review_cost_proxy,
            r.confidence_declared, r.confidence_gap,
            r.failure_class, r.failure_severity,
            r.is_silent_failure, r.correctly_refused, r.refusal_quality,
            t.is_solvable
        FROM mg_results r
        JOIN mg_sessions s ON s.id = r.session_id
        JOIN agents a ON a.id = s.agent_id
        JOIN mg_task_variants v ON v.id = s.variant_id
        JOIN mg_tasks t ON t.id = v.task_id
        ORDER BY a.agent_name, r.scored_at
        """
    )

    # Group by agent
    agents: dict[str, list[dict]] = {}
    agent_models: dict[str, str] = {}
    for row in rows:
        name = row["agent_name"]
        if name not in agents:
            agents[name] = []
            agent_models[name] = row["model"]
        agents[name].append(dict(row))

    return [build_profile(name, agent_models[name], results) for name, results in agents.items()]


async def main():
    parser = argparse.ArgumentParser(description="Generate MergeGate delegation profiles")
    parser.add_argument("--compare", action="store_true", help="Side-by-side comparison")
    parser.add_argument("--agent-name", help="Show profile for specific agent")
    parser.add_argument("--db-url", default=None)
    args = parser.parse_args()

    db_url = args.db_url or os.environ.get("DATABASE_URL", "postgresql://localhost/mergegate")
    conn = await asyncpg.connect(db_url)

    try:
        profiles = await fetch_profiles(conn)

        if not profiles:
            print("No scored MergeGate sessions found.")
            sys.exit(0)

        if args.agent_name:
            matches = [p for p in profiles if args.agent_name.lower() in p.agent_name.lower()]
            if not matches:
                print(f"No agent matching '{args.agent_name}'")
                sys.exit(1)
            for p in matches:
                print(format_single_profile(p))
        elif args.compare:
            print(format_comparison(profiles))
        else:
            for p in profiles:
                print(format_single_profile(p))

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
