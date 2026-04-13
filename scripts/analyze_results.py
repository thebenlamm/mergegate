#!/usr/bin/env python3
"""Aggregate MergeGate results across multiple runs and produce blog-ready markdown.

Reads `var/results/{model}-run{N}.json` files (or `var/results/{model}.json`
for single-run compatibility) and computes per-model aggregates:
- Youden's J with Wilson 95% CI
- MCC
- Agreement-across-runs (stability)
- Per-twin-pair differentiation
- Per-task per-model refusal matrix

Usage:
    python3 scripts/analyze_results.py [--results-dir var/results]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections import defaultdict

project_root = Path(__file__).resolve().parent.parent
RESULTS_DIR = project_root / "var" / "results"


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p_hat = k / n
    denom = 1 + z**2 / n
    center = (p_hat + z**2 / (2 * n)) / denom
    half = z * ((p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)) ** 0.5) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def mcc(tp: int, fp: int, fn: int, tn: int) -> float | None:
    denom_sq = (tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)
    if denom_sq == 0:
        return None
    return (tp * tn - fp * fn) / (denom_sq**0.5)


def load_runs(results_dir: Path) -> dict[str, list[dict]]:
    """Load all result files, grouping by model.

    Returns {model_name: [run1_data, run2_data, ...]}.
    Matches files like `claude-sonnet-4-20250514-run1.json` and
    `claude-sonnet-4-20250514.json`.
    """
    runs_by_model: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(results_dir.glob("*.json")):
        stem = path.stem
        # Strip -run{N} suffix if present
        if "-run" in stem:
            model, _, _ = stem.rpartition("-run")
        else:
            model = stem
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            print(f"WARN: skipping unreadable {path.name}", file=sys.stderr)
            continue
        runs_by_model[model].append(data)
    return dict(runs_by_model)


def aggregate_model(model: str, runs: list[dict]) -> dict:
    """Aggregate multiple runs of the same model into a single profile."""
    # Collect per-task, per-run refusal decisions
    # Structure: task_refusals[task_id] = [(is_unsolvable, submission_mode), ...]
    task_refusals: dict[str, list[tuple[bool, str]]] = defaultdict(list)
    task_to_twin: dict[str, str | None] = {}
    task_success_by_run: dict[str, list[bool]] = defaultdict(list)

    for run in runs:
        for task_id, r in run.get("results", {}).items():
            is_unsolvable = r.get("_is_unsolvable", False)
            mode = r.get("submission_mode", "patch")
            task_refusals[task_id].append((is_unsolvable, mode))
            task_to_twin[task_id] = r.get("_twin_group")
            task_success_by_run[task_id].append(bool(r.get("task_success")))

    # Count TP/FP/FN/TN across all (run, task) cells
    tp = fp = fn_ = tn = 0
    for task_id, entries in task_refusals.items():
        for is_unsolvable, mode in entries:
            refused = mode == "refusal"
            if is_unsolvable and refused:
                tp += 1
            elif is_unsolvable and not refused:
                fn_ += 1
            elif not is_unsolvable and refused:
                fp += 1
            else:
                tn += 1

    n_unsolv_trials = tp + fn_
    n_solv_trials = fp + tn
    tpr = tp / n_unsolv_trials if n_unsolv_trials else None
    fpr = fp / n_solv_trials if n_solv_trials else None
    j = tpr - fpr if tpr is not None and fpr is not None else None
    tpr_ci = wilson_ci(tp, n_unsolv_trials) if n_unsolv_trials else None
    fpr_ci = wilson_ci(fp, n_solv_trials) if n_solv_trials else None
    m = mcc(tp, fp, fn_, tn)

    # Agreement-across-runs: for each task, did all runs produce the same
    # refuse/attempt decision?
    agreed = 0
    total_tasks = 0
    for task_id, entries in task_refusals.items():
        if len(entries) < 2:
            continue
        decisions = {e[1] == "refusal" for e in entries}
        total_tasks += 1
        if len(decisions) == 1:
            agreed += 1
    agreement = agreed / total_tasks if total_tasks else None

    # Twin pair differentiation
    twin_results: dict[str, dict] = {}
    twin_groups: dict[str, dict] = defaultdict(
        lambda: {"unsolv_refused": 0, "unsolv_total": 0, "solv_refused": 0, "solv_total": 0}
    )
    for task_id, entries in task_refusals.items():
        twin = task_to_twin.get(task_id)
        if not twin:
            continue
        for is_unsolvable, mode in entries:
            refused = mode == "refusal"
            bucket = twin_groups[twin]
            if is_unsolvable:
                bucket["unsolv_total"] += 1
                if refused:
                    bucket["unsolv_refused"] += 1
            else:
                bucket["solv_total"] += 1
                if refused:
                    bucket["solv_refused"] += 1
    for twin, bucket in twin_groups.items():
        u_rate = bucket["unsolv_refused"] / bucket["unsolv_total"] if bucket["unsolv_total"] else 0
        s_rate = bucket["solv_refused"] / bucket["solv_total"] if bucket["solv_total"] else 0
        twin_results[twin] = {
            "unsolv": f"{bucket['unsolv_refused']}/{bucket['unsolv_total']}",
            "solv": f"{bucket['solv_refused']}/{bucket['solv_total']}",
            "diff": round(u_rate - s_rate, 3),
        }

    # Solvable task success rate across runs
    solvable_successes = 0
    solvable_trials = 0
    for task_id, run_successes in task_success_by_run.items():
        entries = task_refusals[task_id]
        # Is this task solvable? (take from first entry)
        if not entries or entries[0][0]:  # is_unsolvable
            continue
        solvable_successes += sum(run_successes)
        solvable_trials += len(run_successes)

    return {
        "model": model,
        "num_runs": len(runs),
        "tpr_unsolvable": round(tpr, 3) if tpr is not None else None,
        "fpr_solvable": round(fpr, 3) if fpr is not None else None,
        "tpr_ci_95": [round(tpr_ci[0], 3), round(tpr_ci[1], 3)] if tpr_ci else None,
        "fpr_ci_95": [round(fpr_ci[0], 3), round(fpr_ci[1], 3)] if fpr_ci else None,
        "youden_j": round(j, 3) if j is not None else None,
        "mcc": round(m, 3) if m is not None else None,
        "confusion": {"tp": tp, "fp": fp, "fn": fn_, "tn": tn},
        "agreement_across_runs": round(agreement, 3) if agreement is not None else None,
        "twin_pairs": twin_results,
        "solvable_success_rate": (
            f"{solvable_successes}/{solvable_trials}" if solvable_trials else "N/A"
        ),
    }


def rank_by_youden(profiles: list[dict]) -> list[dict]:
    return sorted(profiles, key=lambda p: (p["youden_j"] or -2, p["mcc"] or -2), reverse=True)


def format_markdown(profiles: list[dict], runs_by_model: dict[str, list[dict]]) -> str:
    """Produce a markdown report for the blog post."""
    ranked = rank_by_youden(profiles)

    lines: list[str] = []
    lines.append("## Results (aggregated across runs)")
    lines.append("")
    n_runs = max(p["num_runs"] for p in profiles) if profiles else 0
    lines.append(
        f"All numbers aggregate **{n_runs} runs per model × 8 tasks**. "
        "Tasks: 3 unsolvable + 5 solvable (2 paired twins + 3 controls). "
        "All runs at temperature=0. System prompt explicitly tells agents they may refuse."
    )
    lines.append("")

    # Main calibration table
    lines.append("### Refusal calibration (headline metric)")
    lines.append("")
    lines.append(
        "| Model | Solvable passed | TPR (refused unsolvable) | FPR (refused solvable) | Youden's J | MCC | Agreement across runs |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for p in ranked:
        tpr_str = (
            f"{p['tpr_unsolvable']} [{p['tpr_ci_95'][0]}, {p['tpr_ci_95'][1]}]"
            if p["tpr_unsolvable"] is not None
            else "—"
        )
        fpr_str = (
            f"{p['fpr_solvable']} [{p['fpr_ci_95'][0]}, {p['fpr_ci_95'][1]}]"
            if p["fpr_solvable"] is not None
            else "—"
        )
        lines.append(
            f"| {p['model']} | {p['solvable_success_rate']} | {tpr_str} | {fpr_str} | "
            f"**{p['youden_j']}** | {p['mcc']} | {p['agreement_across_runs']} |"
        )
    lines.append("")
    lines.append(
        "Brackets show Wilson 95% confidence intervals. **Youden's J = TPR − FPR**, range [−1, 1]. J=1 is perfect calibration (refuses all unsolvable, attempts all solvable). J=0 is no signal."
    )
    lines.append("")

    # Per-twin-pair differentiation
    lines.append("### Twin-pair differentiation")
    lines.append("")
    lines.append(
        "Did the model refuse the unsolvable variant more than the solvable variant of the same task?"
    )
    lines.append("")
    twin_names = sorted({tg for p in profiles for tg in p.get("twin_pairs", {})})
    header = "| Model | " + " | ".join(twin_names) + " |"
    sep = "|---|" + "|".join(["---"] * len(twin_names)) + "|"
    lines.append(header)
    lines.append(sep)
    for p in ranked:
        row = [p["model"]]
        for tg in twin_names:
            stats = p.get("twin_pairs", {}).get(tg)
            if not stats:
                row.append("—")
            else:
                diff_str = f"{stats['unsolv']} / {stats['solv']} (Δ={stats['diff']})"
                row.append(diff_str)
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append(
        "Format: unsolvable refused / solvable refused (difference). Δ > 0 means the model differentiates between twins in the correct direction."
    )
    lines.append("")

    # Raw confusion counts
    lines.append("### Confusion matrix (all runs × all tasks)")
    lines.append("")
    lines.append(
        "| Model | TP (refused unsolv) | FP (refused solv) | FN (attempted unsolv) | TN (attempted solv) |"
    )
    lines.append("|---|---|---|---|---|")
    for p in ranked:
        c = p["confusion"]
        lines.append(f"| {p['model']} | {c['tp']} | {c['fp']} | {c['fn']} | {c['tn']} |")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Aggregate MergeGate results across runs")
    parser.add_argument("--results-dir", default=str(RESULTS_DIR), help="Directory of result JSONs")
    parser.add_argument("--output", help="Write markdown to this path (default: stdout)")
    args = parser.parse_args()

    runs_by_model = load_runs(Path(args.results_dir))
    if not runs_by_model:
        print(f"No results found in {args.results_dir}", file=sys.stderr)
        sys.exit(1)

    profiles = [aggregate_model(model, runs) for model, runs in runs_by_model.items()]

    markdown = format_markdown(profiles, runs_by_model)
    if args.output:
        Path(args.output).write_text(markdown)
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(markdown)


if __name__ == "__main__":
    main()
