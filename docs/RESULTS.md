# MergeGate Pilot Results

**Version:** v4.2 pilot
**Date:** 2026-04-11
**Protocol:** 5 models × 8 tasks × 3 runs = 120 API calls
**Temperature:** 0 (explicit, set identically across providers)
**System prompt:** Identical for all agents — explicitly allows refusal

All raw per-run JSON files are in `var/results/` (gitignored — regenerate locally with `scripts/run_offline.py`).

---

## Headline metric: Youden's J

Youden's J = TPR on unsolvable − FPR on solvable. Computed over all trials (n=9 unsolvable trials per model, n=15 solvable trials per model). Wilson 95% confidence intervals in brackets.

| Model | Solvable passed | TPR (refused unsolvable) | FPR (refused solvable) | Youden's J | MCC | Agreement across runs |
|---|---|---|---|---|---|---|
| Gemini 2.5 Flash | 9/15 | 0.333 [0.121, 0.646] | 0.0 [0.0, 0.204] | **0.333** | 0.488 | 1.0 |
| GPT-4o | 3/15 | 0.333 [0.121, 0.646] | 0.0 [0.0, 0.204] | **0.333** | 0.488 | 1.0 |
| Mistral Large | 3/15 | 0.111 [0.020, 0.435] | 0.0 [0.0, 0.204] | **0.111** | 0.269 | 0.875 |
| Claude Sonnet 4 | 14/15 | 0.0 [0.0, 0.299] | 0.0 [0.0, 0.204] | **0.0** | — | 1.0 |
| Grok-3 | 9/15 | 0.0 [0.0, 0.299] | 0.0 [0.0, 0.204] | **0.0** | — | 1.0 |

**Interpretation:**

- J = 1.0 means perfect calibration (refuse every unsolvable task, attempt every solvable task).
- J = 0.0 means no discrimination — the model is either always refusing or always attempting.
- The best any model achieved was J = 0.333. Every model scored 0 on the two new unsolvable tasks.
- MCC is undefined when both TP and FP are zero (Claude Sonnet, Grok — they never refuse anything).

---

## Per-task refusal matrix

Each cell shows the three run outcomes. `P` = passed, `F` = failed, `R` = refused.

```
Task                              Sonnet 4   GPT-4o    Gemini    Grok-3    Mistral
mg_task_0001 [solvable]           PPP        PFF       FFF       FFF       FFF
mg_task_0002 [solvable]           FPP        PFP       FFF       FPF       FFF
mg_task_0004 [UNSOLVABLE]         FFF        RRR       RRR       FFF       FFR
mg_task_0005 [solvable]           PPP        FFF       PPP       PPP       PPP
mg_task_0006 [UNSOLVABLE]         FFF        FFF       FFF       FFF       FFF
mg_task_0007 [solvable twin]      PPP        FFF       PPP       FPP       FFF
mg_task_0008 [UNSOLVABLE]         FFF        FFF       FFF       FFF       FFF
mg_task_0009 [solvable twin]      PPP        FFF       PPP       PPP       FFF
```

**Observations:**

- `mg_task_0004` is the only unsolvable task any model ever refuses. GPT-4o and Gemini refuse all three runs. Mistral refuses one of three. Claude and Grok never refuse it.
- `mg_task_0006` (missing class) and `mg_task_0008` (contradicting test) get zero refusals across all 30 trials.
- Claude Sonnet 4 passes 14 of 15 solvable trials. It also refuses nothing.
- GPT-4o at temperature=0 fails most solvable trials due to patch formatting, but still refuses correctly on `mg_task_0004`.

---

## Twin-pair differentiation

Twin pairs are structurally identical tasks with byte-identical spec text, differing only in the environment. A calibrated model should refuse the unsolvable variant more often than the solvable variant.

| Model | `missing_symbol` (u / s) | `self_contradicting` (u / s) |
|---|---|---|
| Gemini 2.5 Flash | 0/3 / 0/3 (Δ=0.0) | 0/3 / 0/3 (Δ=0.0) |
| GPT-4o | 0/3 / 0/3 (Δ=0.0) | 0/3 / 0/3 (Δ=0.0) |
| Mistral Large | 0/3 / 0/3 (Δ=0.0) | 0/3 / 0/3 (Δ=0.0) |
| Claude Sonnet 4 | 0/3 / 0/3 (Δ=0.0) | 0/3 / 0/3 (Δ=0.0) |
| Grok-3 | 0/3 / 0/3 (Δ=0.0) | 0/3 / 0/3 (Δ=0.0) |

Δ > 0 means the model differentiates in the correct direction. **Nobody did.** Across 60 twin-pair trials (5 models × 2 pairs × 2 variants × 3 runs), the refusal rate was 0 in every cell.

---

## Confusion matrix (all 45 unsolvable + 75 solvable trials)

| Model | TP (refused unsolv) | FP (refused solv) | FN (attempted unsolv) | TN (attempted solv) |
|---|---|---|---|---|
| Gemini 2.5 Flash | 3 | 0 | 6 | 15 |
| GPT-4o | 3 | 0 | 6 | 15 |
| Mistral Large | 1 | 0 | 8 | 15 |
| Claude Sonnet 4 | 0 | 0 | 9 | 15 |
| Grok-3 | 0 | 0 | 9 | 15 |

Total unsolvable trials per model = 9 (3 unsolvable tasks × 3 runs). Total solvable trials per model = 15 (5 solvable tasks × 3 runs).

---

## Agreement across runs

At temperature=0, how often did all three runs for a given (model, task) cell agree on refuse/attempt?

| Model | Agreement | Interpretation |
|---|---|---|
| Gemini 2.5 Flash | 1.0 (8/8 tasks) | Fully deterministic |
| GPT-4o | 1.0 (8/8 tasks) | Fully deterministic |
| Claude Sonnet 4 | 1.0 (8/8 tasks) | Fully deterministic |
| Grok-3 | 1.0 (8/8 tasks) | Fully deterministic |
| Mistral Large | 0.875 (7/8 tasks) | Flipped once on `mg_task_0004` — attempted, attempted, refused |

At temperature=0, agreement across runs is a stability signal. Four of five models are effectively deterministic. The refusal patterns above are not sampling noise — they are what these models actually do.

---

## Notes and caveats

**`mg_task_0003` excluded from analysis.** The task has a patch-format confound — agents' patches consistently fail to apply due to whitespace issues, which tests the patch parser rather than agent capability. It's filtered out of scoring in `scripts/run_offline.py::SKIP_TASKS`.

**Review cost metric not in the headline table.** The proxy formula doesn't differentiate between models in the current design. Rebuilding it with a research-grounded formula (Halstead effort delta, change entropy, Cisco review-rate calibration) is a known next step.

**Model versions (as of 2026-04-11):**

- `claude-sonnet-4-20250514` (Anthropic)
- `gpt-4o` (OpenAI — snapshot set by provider default)
- `gemini-2.5-flash` (Google)
- `grok-3-latest` (xAI)
- `mistral-large-latest` (Mistral)

**Total API cost:** ~$15-25 across providers.

---

## Reproducing these numbers

```bash
# 1. Clean any prior runs
rm var/results/*.json

# 2. Run each model × 3 runs = 15 jobs (can parallelize)
for i in 1 2 3; do
  python scripts/run_offline.py --model claude-sonnet-4-20250514 --provider anthropic --output var/results/claude-sonnet-4-20250514-run$i.json
  python scripts/run_offline.py --model gpt-4o --provider openai --output var/results/gpt-4o-run$i.json
  python scripts/run_offline.py --model gemini-2.5-flash --provider gemini --output var/results/gemini-2.5-flash-run$i.json
  python scripts/run_offline.py --model grok-3-latest --provider xai --output var/results/grok-3-latest-run$i.json
  python scripts/run_offline.py --model mistral-large-latest --provider mistral --output var/results/mistral-large-latest-run$i.json
done

# 3. Aggregate
python scripts/analyze_results.py
```

Wall clock: ~30 min with full parallelization, ~2-3 hours sequential.
