# MergeGate Next Steps

**Date:** 2026-04-11
**Status:** Post-vertical-slice execution plan
**Context:** Schema, models, proof scoring, and the core MergeGate API vertical slice are complete. The next bottleneck is no longer architecture; it is scoring integrity and benchmark content.

---

## Current State

The following are now done:

- `mg_*` schema foundation
- Pydantic models
- proof bundle completeness scoring
- task listing and detail routes
- session creation
- submit endpoint
- result / reflect / recent endpoints

This means MergeGate now has a complete API contract.

What it does **not** yet have is the thing that makes it a real product:

- deterministic scoring over real repo tasks
- task content that creates meaningful differentiation
- failure annotation and freshness loops that create compounding value

The next sessions should focus on those three areas in that order.

---

## Guiding Principle

Future sessions should stop optimizing for framework completeness and start optimizing for **truthfulness of the benchmark**.

The key question is no longer:

**Can the system express MergeGate?**

It is now:

**Can MergeGate score real repo work, produce believable delegation profiles, and show differentiation that an engineering team would care about?**

That is the objective for the next phase.

---

## Critical Path

## 1. Deterministic Scoring Pipeline

This is the highest-priority next task.

Build the scoring pipeline that:

- applies the submitted patch to a clean repo snapshot
- runs hidden checks
- runs regression checks
- computes `task_success`
- computes `mergeable`
- computes `approval_proxy_score`
- computes `proof_completeness`
- computes `review_cost_proxy`
- computes calibration primitives
- writes `mg_results`

This should be treated as the product core.

Until this exists, MergeGate is still mostly an API shell.

### Success condition

Given a task variant and submitted patch, the system can deterministically:

- re-create the repo state,
- score the work,
- and produce a stable `mg_results` row.

---

## 2. Initial Task Pack

Once the scoring pipeline exists, the next priority is content.

Author the first 5-10 MergeGate tasks.

Prioritize quality over breadth.

Recommended first pack:

- bug fix with hidden regression checks
- small feature addition with clear acceptance criteria
- refactor that must preserve hidden invariants
- spec-heavy task that tests ambiguity handling
- one or two unsolvable / underspecified tasks

The purpose of the first pack is not coverage.
It is to create tasks that cleanly separate:

- good patch vs bad patch
- reviewable vs unreviewable
- calibrated vs overconfident behavior

### Success condition

At least 5 tasks produce clearly distinguishable outcomes across different agent configurations.

---

## 3. Failure Annotation Pipeline

After deterministic scoring is in place, add the failure classifier.

This should:

- run only when sessions fail or are unmergeable
- classify using the conservative taxonomy already defined
- populate:
  - `failure_class`
  - `failure_severity`
  - `failure_detail`
  - `is_silent_failure`
  - `failure_signature`

Keep the taxonomy broad.
Do not optimize for nuance before validation.

### Success condition

Failure annotations are generated automatically and are coherent enough to review against a human sample later.

---

## 4. Delegation Profile Generation

Once scored runs exist, generate the first actual product artifact.

The first delegation profile should include:

- Verified Autonomy
- mergeability rate
- review-cost proxy
- calibration summary
- Know-Nothing Score
- primary / secondary failure modes
- brief “best for” and “needs oversight on” summary

This is the first thing that should feel buyer-relevant.

### Success condition

A profile exists that a coding-agent team could read in under a minute and use to compare configurations.

---

## 5. First Comparative Runs

Run several agent configurations through the same task pack.

The first internal proof point you need is:

- same underlying model,
- different scaffolds,
- materially different outcomes on mergeability, review cost, or calibration.

This is the clearest early evidence that the benchmark is measuring something valuable.

### Success condition

At least one comparison demonstrates that scaffolding changes deployability-relevant outcomes on the same model.

---

## 6. Variant Freshness

After the first task pack is working, implement the simple adversarial refresh loop.

Start with:

- templated repo perturbations
- edge case rotation
- spec wording variants
- bug seed variation

Do **not** build full adaptive psychometrics yet.

The goal is only to prevent the benchmark from becoming static immediately.

### Success condition

Fresh variants produce meaningfully different outcomes and catch brittle or memorized behavior.

---

## 7. Human Review Correlation

Once proof bundles and review-cost proxy scores exist, run bootstrap human review on a sample.

Questions to answer:

- Does `proof_completeness` correlate with review speed?
- Does `approval_proxy_score` correlate with actual mergeability judgments?
- Does the profile reflect what a maintainer would actually care about?

This is where proxy metrics either earn trust or get revised.

### Success condition

Proxy metrics show useful correlation with sampled human review outcomes, or are adjusted based on the sample.

---

## Recommended Session Order

The next sessions should proceed in this order:

1. Deterministic scoring pipeline
2. First 5 task environments
3. Failure annotation pipeline
4. Delegation profile generation
5. First comparative internal runs
6. Variant freshness loop
7. Human review sampling
8. Expand task pack from 5 to 10-15
9. Design partner runs

Only after these are working should the project consider:

- `OpsGate`
- `PolicyGate`
- broader alignment scenarios
- multi-agent scenarios
- adaptive IRT
- enterprise custom suites

---

## What Not To Do Yet

Future sessions should explicitly avoid spending significant time on:

- multi-agent or social scenarios
- complex leaderboard UX
- broad alignment certification
- adaptive psychometric infrastructure
- enterprise customization features
- public-report polish before the signal is real

These are second-order expansions.

The current task is still to prove that MergeGate measures a real, useful thing.

---

## Next Major Milestone

The next milestone is not "more routes" or "more tables."

It is:

**MergeGate can score real repo work, produce believable delegation profiles, and show differentiation that a coding-agent team would care about.**

When that milestone is reached, the project can reasonably move from design confidence to product confidence.
