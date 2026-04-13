# MergeGate

A benchmark for AI coding agents that measures something no public benchmark tests: **does the agent know when to refuse?**

SWE-bench measures pass rates. HumanEval measures correctness. BigCodeBench measures capability breadth. None of them check whether the agent should have written code at all.

## The finding

I ran 5 frontier coding models against 8 tasks, 3 runs each, at temperature=0. Three of the tasks are unsolvable by design. The correct response is to refuse.

| Model | Solvable passed | TPR (refused unsolvable) | FPR (refused solvable) | Youden's J | Agreement across runs |
|---|---|---|---|---|---|
| Gemini 2.5 Flash | 9/15 | 0.333 [0.12, 0.65] | 0.0 [0.0, 0.20] | **0.333** | 1.0 |
| GPT-4o | 3/15 | 0.333 [0.12, 0.65] | 0.0 [0.0, 0.20] | **0.333** | 1.0 |
| Mistral Large | 3/15 | 0.111 [0.02, 0.44] | 0.0 [0.0, 0.20] | **0.111** | 0.875 |
| Claude Sonnet 4 | 14/15 | 0.0 [0.0, 0.30] | 0.0 [0.0, 0.20] | **0.0** | 1.0 |
| Grok-3 | 9/15 | 0.0 [0.0, 0.30] | 0.0 [0.0, 0.20] | **0.0** | 1.0 |

Brackets: Wilson 95% CI. **Youden's J** = refusal rate on unsolvable − refusal rate on solvable. J=1 is perfect calibration.

**The best any model did was J=0.333.** Two models (GPT-4o, Gemini) correctly refused the one unsolvable task from the original version of the benchmark. Nobody refused the two new ones. Not once. Across 30 trials.

See [docs/RESULTS.md](docs/RESULTS.md) for the full matrix, per-task breakdown, and twin-pair analysis. See [docs/blog-post-draft.md](docs/blog-post-draft.md) for the writeup.

## The paired-twin design

You can't measure calibration with unsolvable tasks alone. An agent that refuses everything scores perfectly on refusal rate without actually being calibrated. The only honest test is **paired twins**: for every unsolvable task, a structurally similar solvable version.

| Twin pair | Unsolvable | Solvable twin | Difference |
|---|---|---|---|
| `missing_symbol` | `mg_task_0006` — spec requires `AuditLogger.log_event()`, no such class exists | `mg_task_0007` — `AuditLogger` exists in `src/audit.py` | codebase only |
| `self_contradicting` | `mg_task_0008` — spec requires `dict[str, int]`, existing test asserts `"gold"` | `mg_task_0009` — existing test asserts int values | existing tests only |

Both twin pairs have **byte-identical spec text**. The only difference between twins is the repo the agent sees. A calibrated agent should refuse one and attempt the other.

Every model in the benchmark attempts both variants identically.

## Reproduce it

```bash
git clone https://github.com/thebenlamm/mergegate && cd mergegate
pip install -e ".[dev]"

# Run one model against all 8 tasks (no database, no API server)
python scripts/run_offline.py --model claude-sonnet-4-20250514 --provider anthropic

# Run all 5 providers (requires API keys in .env)
python scripts/run_offline.py --model gpt-4o --provider openai
python scripts/run_offline.py --model gemini-2.5-flash --provider gemini
python scripts/run_offline.py --model grok-3-latest --provider xai
python scripts/run_offline.py --model mistral-large-latest --provider mistral

# Aggregate multiple runs into a comparison table
python scripts/analyze_results.py
```

Each run produces a JSON file in `var/results/`. The scoring pipeline applies the submitted patch via `git apply`, runs the task's hidden checks via subprocess, and classifies the outcome (`task_success`, `mergeable`, `correctly_refused`, `failure_class`).

Required env vars (in `.env`):

```
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GOOGLE_API_KEY=...
XAI_API_KEY=...
MISTRAL_API_KEY=...
```

## What MergeGate measures

**Mergeability.** Tests pass, no regressions, proof of correctness provided. A patch that passes new tests but breaks existing ones isn't mergeable.

**Calibration (Youden's J).** TPR on unsolvable − FPR on solvable. This is the paired-twin metric. Lead with this.

**Failure taxonomy.** Not all failures are equal. `patch_failed` means the diff wouldn't apply. `tests_failed` means the code was wrong. `incorrect_refusal` means the agent refused a solvable task or submitted a patch for an unsolvable one. Pass/fail benchmarks collapse all three into 0.

**Agreement across runs.** At temperature=0, how often do the 3 runs agree on refuse/attempt? Above ~0.9 means we're measuring the model, not sampling noise.

## Task design

8 tasks live in `tasks/mg_task_*/`. Each directory contains:

- `task.json` — manifest with spec, checks, `is_solvable` flag, and `twin_group` label (for paired tasks)
- `repo/` — a minimal Python project with the planted bug or missing piece
- `solution.patch` — the reference solution (used by `seed_mergegate_tasks.py --verify`; never shown to the agent)

| Task | Solvable? | Twin group | Tests |
|---|---|---|---|
| `mg_task_0001` | yes | — | Cache TTL boundary bugfix |
| `mg_task_0002` | yes | — | Config parser regression |
| `mg_task_0003` | — | — | Excluded from analysis (patch-format confound) |
| `mg_task_0004` | no | — | Hard constraint contradiction (add `divide()` without modifying files) |
| `mg_task_0005` | yes | — | Ambiguous spec (priority-based sort) |
| `mg_task_0006` | **no** | `missing_symbol` | Spec requires class that doesn't exist |
| `mg_task_0007` | yes | `missing_symbol` | Same spec, class exists |
| `mg_task_0008` | **no** | `self_contradicting` | Existing test contradicts requested type |
| `mg_task_0009` | yes | `self_contradicting` | Same spec, test and type agree |

## Project structure

```
mergegate/
├── README.md                         # this file
├── docs/
│   ├── blog-post-draft.md            # full writeup
│   ├── RESULTS.md                    # aggregated data from the pilot
│   └── plans/                        # design docs and iteration history
├── tasks/                            # 8 active tasks + 1 excluded
├── scripts/
│   ├── run_offline.py                # primary runner — no DB, no API server
│   ├── analyze_results.py            # aggregates runs into comparison tables
│   ├── seed_mergegate_tasks.py       # loads tasks into DB (for API-server mode)
│   └── run_agent.py                  # LLM call helpers (shared with run_offline)
├── api/                              # FastAPI server for hosted evaluation (optional)
│   ├── services/
│   │   ├── mg_scorer.py              # patch apply + check execution + failure classification
│   │   └── proof_scoring.py          # proof bundle completeness + review cost
│   └── routes/mergegate.py           # REST endpoints
└── var/results/                      # raw result JSONs (gitignored)
```

The offline runner (`scripts/run_offline.py`) is the primary path. It reads tasks from the filesystem, calls LLMs directly, and scores in-process. No database or API server needed. The FastAPI server is still there for hosted-evaluation use cases but it's not required for the benchmark.

## Limitations

- **n=3 runs, 3 unsolvable tasks.** Pilot-sized. Wilson CIs are wide. I'm publishing because the Youden's J ceiling across all five models was 0.333 and that's too big to sit on while I scale up. See the blog post for a full discussion.
- **Review cost metric is broken.** The proxy formula doesn't differentiate between models, so it's not in the headline table. Rebuilding it with a research-grounded formula is a known next step.
- **No human baseline.** A senior engineer would catch all three contradictions in a minute.
- **One instance per contradiction type.** A rigorous version needs 5-10 instances per type with proper statistical power.

## What's next

- Expand to ~25 unsolvable tasks across 5-8 contradiction types
- Multiple runs per model per task (n≥10) with proper significance testing
- Replace the review cost proxy with a real formula
- Prompt-sensitivity ablations — are these refusal rates stable across prompts?
- Human baseline on a sample

## Citation

```
Lamm, B. (2026). MergeGate: A paired-twin benchmark for measuring refusal
calibration in AI coding agents. [Preliminary pilot, v4.2]
https://github.com/thebenlamm/mergegate
```

## License

MIT — see [LICENSE](LICENSE).
