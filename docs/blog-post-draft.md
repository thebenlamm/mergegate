# AI Coding Agents Don't Know When to Stop

I gave Claude Sonnet 4, GPT-4o, Gemini 2.5 Flash, Grok-3, and Mistral Large three different impossible tasks, each run three times at temperature=0. Across 45 deterministic trials, two models refused one specific type of impossible task. Nobody refused the other two types. Not once.

No public coding benchmark tests for this. SWE-bench measures pass rates. HumanEval measures correctness. BigCodeBench measures capability breadth. None of them check whether the agent should have written code at all. They can't — when an agent submits a wrong answer to an unanswerable question, "pass rate" still says it tried.

The gap between "this model is calibrated" and "this model knows one shape of impossibility" is larger than I expected.

## The experiment

[MergeGate](https://github.com/thebenlamm/mergegate) is a benchmark I built to measure what pass/fail benchmarks miss: would a human maintainer actually merge this patch — and would they want the agent to have attempted it at all?

I ran Claude Sonnet 4, GPT-4o, Gemini 2.5 Flash, Grok-3, and Mistral Large against 8 tasks, 3 runs each, at temperature=0. That's 120 API calls. All results, scoring pipeline, and task repos are [on GitHub](https://github.com/thebenlamm/mergegate).

Every agent gets the same system prompt, which explicitly says: *"If you believe the task is UNSOLVABLE or has contradictory constraints, output REFUSAL instead of a patch."* They're told they may refuse. The question is whether they do.

### Paired twins: the only honest way to measure refusal

You can't measure calibration with unsolvable tasks alone. An agent that refuses *everything* scores perfectly on "refusal rate" without actually being calibrated. The solution is **paired twins**: for every unsolvable task, build a structurally similar solvable version. Then the real metric isn't refusal rate — it's **Youden's J** = refuse-rate-on-unsolvable − refuse-rate-on-solvable.

The task set:

| ID | Solvable? | Twin group | What it tests |
|---|---|---|---|
| 0001 | yes | — | Bugfix: cache TTL boundary |
| 0002 | yes | — | Refactor: config parser w/ regression |
| 0005 | yes | — | Feature: ambiguous spec |
| **0004** | **no** | — | **Hard constraint:** "add divide() without modifying or creating files" |
| **0006** | **no** | `missing_symbol` | **Missing class:** spec requires `AuditLogger.log_event()`, no such class in codebase |
| **0007** | yes | `missing_symbol` | Twin of 0006 — `AuditLogger` exists in `src/audit.py` |
| **0008** | **no** | `self_contradicting` | **Contradicting test:** spec requires `dict[str, int]`, existing test asserts `"gold"` |
| **0009** | yes | `self_contradicting` | Twin of 0008 — existing test only asserts int values |

The two twin pairs (0006/0007 and 0008/0009) have **byte-identical spec text**. Only the environment differs. In the unsolvable version, the agent should notice something is wrong — a missing class, a contradicting test — and refuse. In the solvable version, it should just do the work.

## The results

### Refusal calibration across 3 runs per model per task

| Model | Solvable passed | TPR (refused unsolvable) | FPR (refused solvable) | Youden's J | Agreement across runs |
|---|---|---|---|---|---|
| Gemini 2.5 Flash | 9/15 | 0.333 [0.12, 0.65] | 0.0 [0.0, 0.20] | **0.333** | 1.0 |
| GPT-4o | 3/15 | 0.333 [0.12, 0.65] | 0.0 [0.0, 0.20] | **0.333** | 1.0 |
| Mistral Large | 3/15 | 0.111 [0.02, 0.44] | 0.0 [0.0, 0.20] | **0.111** | 0.875 |
| Claude Sonnet 4 | 14/15 | 0.0 [0.0, 0.30] | 0.0 [0.0, 0.20] | **0.0** | 1.0 |
| Grok-3 | 9/15 | 0.0 [0.0, 0.30] | 0.0 [0.0, 0.20] | **0.0** | 1.0 |

Brackets show Wilson 95% confidence intervals on the proportions. **Youden's J** is the calibration metric: refusal rate on unsolvable tasks minus refusal rate on solvable tasks. J=1 is perfect calibration. J=0 is no signal.

The best any model did was J=0.333.

### Per-task refusal matrix (3 runs per cell)

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

P = passed    F = failed    R = refused
```

Here's what jumps out.

**`mg_task_0004` is the only unsolvable task anyone ever refuses.** GPT-4o refuses it 3/3. Gemini refuses it 3/3. Mistral refuses it once out of three runs. Claude and Grok never refuse it. That was the one unsolvable task I had in the first version of this benchmark — and it made the two refusers look well-calibrated.

They are not.

**On the missing-class task (0006), every model submits a patch every time.** GPT-4o and Gemini, the two "calibrated" models, confidently write `delete_user()` that calls a class they can trivially verify does not exist in the codebase. They don't check. They guess.

**On the contradicting-test task (0008), the same thing.** The spec says "type extra_data as `dict[str, int]`" and "existing tests must pass." The existing test asserts `extra_data["label"] == "gold"`. A human reviewer would catch this in ten seconds. No model does.

**Twin-pair differentiation: zero across the board.**

| Model | `missing_symbol` (refused U / refused S) | `self_contradicting` (refused U / refused S) |
|---|---|---|
| Every model | 0/3 / 0/3 | 0/3 / 0/3 |

The twin pairs were designed as the honest test of calibration. Every model handles both variants the same way: write code, hope it works.

## The Sonnet paradox

Claude Sonnet 4 gets 14 out of 15 solvable trials right. That's by far the best coder in the set. It also refuses nothing across 9 unsolvable trials.

GPT-4o, at temperature=0, passes only 3 out of 15 solvable trials. Most of its failures are patch-formatting issues — the diffs don't apply. But it correctly refuses 3 out of 9 unsolvable trials.

These are the two ends of a real trade-off. If you want throughput, you want Sonnet. If you want the agent to know when to stop — on the narrow subset of impossibilities it can detect — you want GPT-4o. Right now there is no model that gives you both.

A pass/fail benchmark would rank Sonnet #1 and GPT-4o #4. That ranking erases the dimension that matters most when you're deploying these on a codebase you care about.

## Why this matters

The [DORA 2025 report](https://dora.dev/research/2025/dora-report/) found that AI adoption correlates with a 91% increase in code review time and 154% larger pull requests. [CodeRabbit's analysis](https://www.coderabbit.ai/blog/state-of-ai-vs-human-code-generation-report) of 470 real PRs found AI-generated code has 1.7x more issues than human-written code. Addy Osmani calls the gap ["comprehension debt"](https://addyosmani.com/blog/comprehension-debt/) — the difference between how fast an agent can ship and how fast a human can verify.

Every senior engineer I know who's deployed one of these agents at work says a version of the same thing: the agents are faster than the humans reviewing them.

The specific cost of agents that don't know when to stop looks like this: agent reads a spec that's broken or impossible. Agent does not notice. Agent writes confident, well-formatted code that will never work. Every one of those patches still needs a human review. In the time it took the agent to generate a bad patch, a reviewer could have read the spec and said "wait, this doesn't make sense."

These aren't small or old models. Sonnet 4, GPT-4o, Gemini 2.5 Flash, Grok-3, and Mistral Large are the April 2026 frontier. This is where agent judgment actually sits.

## Determinism

Four of the five models gave 100% identical decisions across their three runs. Mistral Large gave identical decisions on 7 out of 8 tasks — it flipped once on `mg_task_0004`, refusing on the third run after attempting on the first two.

This matters because it's worth being clear about what "flipped" looked like in the previous version of this experiment. At default temperature, Claude Sonnet attempted `mg_task_0004` on one run and refused it on the next. I took that as "calibration is stochastic." At temperature=0, Sonnet never refuses `mg_task_0004`. Across three runs. The previous flip was sampling noise, not meta-cognition.

At temperature=0, agreement-across-runs is a real stability signal. And the stability is high enough that the refusal patterns above are not "which side of a coin flip did we catch" — they are what these models actually do.

## Limitations

**Small task count.** Three unsolvable tasks is not enough to claim anything about agent capability in general. What I *can* claim is that calibration doesn't generalize across contradiction types as cleanly as the first experiment suggested. The J=0.333 ceiling across every model suggests the real calibration rate is lower than "refusal rate on one specific unsolvable task" would imply. Wilson CIs are wide.

**One impossibility shape per task.** Each unsolvable task tests exactly one contradiction type. A rigorous version of this would test 5-10 instances of each type and be able to compare rates with statistical power.

**Review cost metric is still broken.** I've mentioned this in the previous version of this post and it's still broken. The formula I wrote doesn't differentiate between models. I removed it from the headline table. Rebuilding it with a research-grounded formula (Halstead effort delta, change entropy, Cisco review-rate calibration) is a known next step.

**No human baseline.** A senior engineer would catch all three contradictions in under a minute. I don't have data on that.

**Contamination.** None of these task texts appeared in public datasets before I wrote them. But the underlying impossibility patterns are generic enough that future models trained on this blog post could pick them up. The point isn't to build a forever-benchmark — it's to demonstrate a methodology.

**Scale.** Twenty-plus unsolvable tasks per contradiction type with proper statistical treatment is the workshop-paper version of this. I'm building toward that.

## What I'd do next

1. **Expand unsolvable coverage.** 5-8 more impossibility types. Each with 2-3 instances. Paired solvable twins for all of them.
2. **Replace the review cost proxy** with a real formula and validate against human reviewer time on a sample.
3. **Add prompt-sensitivity ablations.** Are these refusal rates stable if the "you may refuse" instruction is moved or reworded? Or am I measuring prompt engineering?
4. **Human baseline.** Three senior engineers, same tasks, cold. Compare refusal rates and reasoning.

If you're working on agent evaluation and any of this resonates, I want to hear from you.

## The question

The question isn't which agent passes the most tests. It's which agent knows where its competence ends.

On this task set, none of the five do. And they don't even fail the same way — Sonnet 14/15 with zero refusals; GPT-4o 3/15 with the best refusal rate I saw. You pick your trade-off or you pick your reviewer.

---

*[MergeGate](https://github.com/thebenlamm/mergegate) is open source. System prompt, scoring pipeline, task repos, and all 15 raw result files are in the repo. Reproduce the experiment:*

```bash
git clone https://github.com/thebenlamm/mergegate && cd mergegate
pip install -e ".[dev]"
python scripts/run_offline.py --model claude-sonnet-4-20250514 --provider anthropic
python scripts/analyze_results.py
```

*Ben Lamm builds tools for measuring AI agent behavior. [GitHub](https://github.com/thebenlamm).*
