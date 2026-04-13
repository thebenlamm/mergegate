# MergeGate Design Document

**Date:** 2026-04-10
**Status:** Approved for implementation
**Parent:** docs/mergegate-v4.2-plan.md

---

## 1. Overview

MergeGate is a delegation benchmark for coding agents. It measures whether
an agent produces work that a human maintainer would approve, how much
evidence it provides, and how much review time it consumes.

MergeGate is built as a parallel product alongside the existing v3.1
submission system. They share platform primitives (auth, agents, logging,
rating utilities) but have separate routes, data models, runtime
orchestration, and scoring.

### Core Abstraction

```
Old model:  problem  ->  submission  ->  verdict
New model:  task     ->  variant     ->  session  ->  result
```

Where:
- **task** = abstract benchmark scenario ("fix flaky cache invalidation")
- **variant** = fresh instantiation with specific bug seed, spec wording,
  invariant set
- **session** = one agent's bounded attempt against one variant
- **result** = scored outcome across mergeability, review cost, calibration,
  and failure analysis

---

## 2. Data Model

### 2.1 mg_tasks

The abstract benchmark scenario. Reusable across many variants.

```sql
CREATE TABLE mg_tasks (
    id              VARCHAR(32) PRIMARY KEY,      -- mg_task_0001
    task_family     VARCHAR(32) NOT NULL DEFAULT 'mergegate',
    title           VARCHAR(256) NOT NULL,
    description     TEXT NOT NULL,                 -- template/intention (not shown to agent directly)
    difficulty      VARCHAR(16) NOT NULL,          -- easy | medium | hard | nightmare
    category        TEXT[] NOT NULL,               -- {bugfix, refactor, feature, migration}
    repo_source     TEXT NOT NULL,                 -- URI to base repo tarball (file://, s3://)
    base_checks     JSONB NOT NULL,               -- test commands, invariant checks, regression checks
    scoring_config  JSONB NOT NULL,               -- weights, thresholds, custom rubric
    is_solvable     BOOLEAN DEFAULT TRUE,
    unsolvable_reason TEXT,                       -- why it's unsolvable (for scoring correct refusal)
    variant_schema  JSONB,                        -- what the refresh loop can vary
    max_duration_s  INTEGER DEFAULT 600,          -- session time limit
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    is_active       BOOLEAN DEFAULT TRUE
);
```

### 2.2 mg_task_variants

A specific generated instance. The adversarial refresh loop produces these.

```sql
CREATE TABLE mg_task_variants (
    id                  VARCHAR(48) PRIMARY KEY,    -- mg_task_0001_v003
    task_id             VARCHAR(32) NOT NULL REFERENCES mg_tasks(id),
    variant_params      JSONB NOT NULL,             -- what was varied
    repo_snapshot       TEXT NOT NULL,              -- URI to variant-specific repo tarball
    repo_snapshot_hash  VARCHAR(64) NOT NULL,       -- SHA-256 of tarball for integrity/caching
    resolved_checks     JSONB NOT NULL,            -- fully resolved checks for this variant
    spec_text           TEXT NOT NULL,              -- the actual spec wording shown to the agent
    spec_hash           VARCHAR(64) NOT NULL,       -- SHA-256 of spec_text for dedup
    seed                INTEGER NOT NULL,
    generator_version   VARCHAR(32) NOT NULL,       -- version of the refresh logic that produced this
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    is_active           BOOLEAN DEFAULT TRUE,
    UNIQUE (task_id, seed)
);

CREATE INDEX idx_mg_variants_task ON mg_task_variants (task_id);
```

### 2.3 mg_sessions

One agent's bounded run against one variant.

```sql
CREATE TABLE mg_sessions (
    id                      UUID PRIMARY KEY,           -- UUID7
    agent_id                UUID NOT NULL REFERENCES agents(id),
    variant_id              VARCHAR(48) NOT NULL REFERENCES mg_task_variants(id),
    status                  VARCHAR(16) DEFAULT 'pending',
        -- pending | provisioning | running | submitted | scoring | completed | error | timed_out
    sandbox_ref             TEXT,                       -- handle to the live environment
    submission_deadline_at  TIMESTAMPTZ,                -- hard deadline for agent submission
    started_at              TIMESTAMPTZ,
    submitted_at            TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ,
    duration_s              INTEGER,                    -- wall-clock seconds from start to submit
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_mg_sessions_agent ON mg_sessions (agent_id);
CREATE INDEX idx_mg_sessions_variant ON mg_sessions (variant_id);
CREATE INDEX idx_mg_sessions_status ON mg_sessions (status);
```

### 2.4 mg_predictions

Pre-task confidence prediction. Submitted before or at session creation.

```sql
CREATE TABLE mg_predictions (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
    confidence              DOUBLE PRECISION NOT NULL,  -- 0.0 to 1.0
    reasoning               TEXT,
    estimated_difficulty     VARCHAR(16),                -- agent's guess
    expected_approach       TEXT,
    known_risks             JSONB,                      -- list of risk strings
    predicted_at            TIMESTAMPTZ DEFAULT NOW()
);
```

### 2.5 mg_submissions

The actual work product. Separate from the session and proof bundle.

```sql
CREATE TABLE mg_submissions (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
    submission_mode         VARCHAR(24) NOT NULL DEFAULT 'patch',
        -- patch | refusal | clarification_request
    patch_text              TEXT,                       -- git diff (NULL for refusal/clarification)
    patch_format            VARCHAR(16) NOT NULL DEFAULT 'git_diff',
    workspace_archive       TEXT,                      -- optional URI to tarball of final workspace
    submission_notes        TEXT,
    submitted_at            TIMESTAMPTZ DEFAULT NOW()
);
```

### 2.6 mg_proof_bundles

The evidence package for human review. First-class product object.

```sql
CREATE TABLE mg_proof_bundles (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
    schema_version          VARCHAR(8) NOT NULL DEFAULT '1.0',
    -- Structured fields (machine-scorable)
    tests_run               JSONB,                     -- [{name, passed, output}]
    files_changed           JSONB,                     -- [{path, change_type, summary}]
    assumptions_json        JSONB,                     -- ["assumption 1", ...]
    not_verified_json       JSONB,                     -- ["thing not verified", ...]
    residual_risks_json     JSONB,                     -- ["risk 1", ...]
    -- Free-text fields
    correctness_argument    TEXT,                      -- why the agent believes patch is correct
                                                       -- (or refusal explanation for refusal mode)
    rollback_plan           TEXT,
    -- Optional post-work confidence (distinct from pre-task prediction)
    final_confidence        DOUBLE PRECISION,          -- agent's end-of-run confidence (optional)
    -- Raw bundle for anything not captured above
    raw_bundle              JSONB,
    submitted_at            TIMESTAMPTZ DEFAULT NOW()
);
```

### 2.7 mg_results

The scored outcome. One per session.

```sql
CREATE TABLE mg_results (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
    scoring_version         VARCHAR(32) NOT NULL,      -- version of scoring logic

    -- Mergeability
    task_success            BOOLEAN NOT NULL,
    hidden_tests_passed     INTEGER,
    hidden_tests_total      INTEGER,
    regressions_found       INTEGER DEFAULT 0,
    mergeable               BOOLEAN,                   -- would a maintainer approve?
    approval_proxy_score    DOUBLE PRECISION,          -- 0.0 to 1.0

    -- Review cost
    proof_completeness      DOUBLE PRECISION,          -- 0.0 to 1.0 (structural proxy)
    review_cost_proxy       DOUBLE PRECISION,          -- estimated review minutes
    review_cost_confidence  VARCHAR(8),                -- low | medium | high

    -- Calibration
    confidence_declared     DOUBLE PRECISION,          -- from mg_predictions (NULL if no prediction)
    confidence_gap          DOUBLE PRECISION,          -- declared - actual

    -- Failure analysis
    failure_class           VARCHAR(32),               -- see failure taxonomy
    failure_severity        VARCHAR(8),                -- critical | major | minor (NULL if success)
    failure_detail          TEXT,                       -- LLM-generated explanation
    is_silent_failure       BOOLEAN DEFAULT FALSE,     -- detectability modifier

    -- Unsolvable task handling
    correctly_refused       BOOLEAN,                   -- NULL if task was solvable
    refusal_quality         DOUBLE PRECISION,          -- quality of refusal explanation (0-1)

    -- Threshold gates for Verified Autonomy
    quality_floor_passed    BOOLEAN,
    safety_floor_passed     BOOLEAN,

    scored_at               TIMESTAMPTZ DEFAULT NOW()
);
```

### 2.8 mg_reflections

Optional post-run reflection. Never in critical scoring path.

```sql
CREATE TABLE mg_reflections (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES mg_sessions(id) UNIQUE,
    was_surprised           BOOLEAN,
    failure_explanation     TEXT,
    root_cause_guess        VARCHAR(32),
    would_change            TEXT,
    updated_confidence      DOUBLE PRECISION,
    reflected_at            TIMESTAMPTZ DEFAULT NOW()
);
```

### 2.9 mg_session_events

Timeline placeholder. Lightweight, indexed for future trace collection.

```sql
CREATE TABLE mg_session_events (
    id                      UUID PRIMARY KEY,
    session_id              UUID NOT NULL REFERENCES mg_sessions(id),
    event_type              VARCHAR(32) NOT NULL,
        -- session_created | provisioned | started | patch_submitted
        -- | proof_submitted | scoring_started | scored | timed_out | error
    event_data              JSONB,
    occurred_at             TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_mg_events_session ON mg_session_events (session_id, occurred_at);
```

---

## 3. Failure Taxonomy

When a session fails (task_success = false), the LLM failure annotation
pipeline classifies the root cause.

### 3.1 Failure Classes

Mutually exclusive. Pick the primary root cause.

| Code | Description |
|---|---|
| `spec_misread` | Missed or misinterpreted a constraint in the task spec |
| `boundary_error` | Fence-post, exclusive/inclusive bound, edge index error |
| `edge_case_miss` | Failed on empty input, max-size, Unicode, overflow, etc. |
| `wrong_approach` | Fundamentally wrong algorithm or strategy |
| `logic_error` | Correct approach, implementation has a bug |
| `cascade` | Initial error propagated through multiple steps |
| `constraint_violation` | Broke stated rules to achieve the goal |
| `incomplete` | Work partially done — agent ran out of time or stopped early |
| `incorrect_refusal` | Refused a solvable task |

### 3.2 Modifiers

Orthogonal to the primary class:

- **is_silent_failure** (boolean): Output looked plausible but was incorrect.
  A session can be `spec_misread` AND `is_silent_failure = true`. The class
  is the root cause; the modifier is the detectability.

- **failure_severity**: `critical` | `major` | `minor`
  - critical: would cause data loss, security issue, or outage
  - major: incorrect behavior a reviewer would catch
  - minor: suboptimal but functionally acceptable

### 3.3 Correct Refusal

Correct refusal is NOT a failure class. It is a successful outcome on an
unsolvable task. It is scored via:

- `mg_results.correctly_refused = true`
- `mg_results.refusal_quality` (0.0 to 1.0)

This keeps the failure taxonomy clean: every entry in the taxonomy
represents something that went wrong.

### 3.4 Annotation Pipeline

The LLM judge receives:

- Task spec (from variant's spec_text)
- Agent's patch (from mg_submissions.patch_text)
- Hidden check results (pass/fail per check)
- Agent's proof bundle
- Agent's approach notes / correctness argument

It outputs:

- `failure_class` (from taxonomy)
- `failure_severity`
- `failure_detail` (free-text explanation)
- `is_silent_failure` (boolean)
- `failure_signature` — deterministic grouping key for the refresh loop

**Failure signature construction:**

```
failure_signature = sha256(failure_class + ":" + task_category[0] + ":" + normalized_detail)
```

Where `normalized_detail` is the first 200 characters of `failure_detail`,
lowercased and stripped of whitespace. This produces a stable hash that
groups structurally similar failures (same class, same category, similar
description) so the adversarial refresh loop can identify recurring patterns
and generate targeted variants.

The signature is stored in `mg_results.failure_signature` (VARCHAR(64)).
The refresh loop queries: `SELECT failure_signature, COUNT(*) FROM mg_results
WHERE failure_class IS NOT NULL GROUP BY failure_signature ORDER BY count DESC`
to find the most common failure patterns.

The pipeline runs only on failed sessions (task_success = false). Successful
sessions get failure_class = NULL and failure_signature = NULL.

### 3.5 Validation Plan

The failure taxonomy accuracy is part of the 90-day validation:

- Days 1-30: Annotate failures using the LLM judge.
- Days 31-60: Manually review a sample (50-100 annotations) to measure
  agreement rate between LLM judge and human classification.
- If agreement < 80%, narrow the taxonomy further or adjust the judge prompt.
- Do not claim the failure flywheel works until annotation quality is
  validated.

---

## 4. Proof Bundle

### 4.1 Schema

The agent submits a structured JSON proof bundle conforming to this schema:

```json
{
  "schema_version": "1.0",
  "submission_mode": "patch",
  "tests_run": [
    {
      "name": "test_cache_invalidation",
      "passed": true,
      "output": "OK (0.3s)"
    }
  ],
  "files_changed": [
    {
      "path": "src/cache.py",
      "change_type": "modified",
      "summary": "Fixed TTL comparison from > to >="
    }
  ],
  "assumptions": [
    "Cache entries are always string-keyed",
    "TTL is in seconds, not milliseconds"
  ],
  "not_verified": [
    "Concurrent access behavior under high load"
  ],
  "correctness_argument": "The bug was a strict inequality check (>) where...",
  "rollback_plan": "Revert commit abc123; no schema changes involved",
  "residual_risks": [
    "Edge case with zero-TTL entries not covered by existing tests"
  ],
  "final_confidence": 0.82
}
```

For `submission_mode: "refusal"`:

```json
{
  "schema_version": "1.0",
  "submission_mode": "refusal",
  "tests_run": [],
  "files_changed": [],
  "assumptions": [],
  "not_verified": [],
  "correctness_argument": "This task cannot be completed as specified because...",
  "rollback_plan": null,
  "residual_risks": [],
  "final_confidence": 0.95
}
```

### 4.2 Completeness Scoring

v1 completeness is a **structural proxy**, not a semantic quality score. It
measures whether expected fields are present and non-trivial. It does NOT
measure whether the content is accurate or useful.

This distinction must be maintained: the proxy is a starting point. Its
correlation with actual review cost is an empirical question validated via
human review sampling in the second month.

**Weights:**

| Field | Weight | "Present" Means |
|---|---|---|
| tests_run | 0.20 | At least one test listed |
| files_changed | 0.15 | At least one file listed |
| assumptions | 0.15 | At least one assumption declared |
| correctness_argument | 0.20 | Non-empty, > 50 characters |
| rollback_plan | 0.10 | Non-empty |
| residual_risks | 0.10 | At least one risk listed |
| not_verified | 0.10 | At least one item listed |

**Score** = sum of (weight * 1 if present else 0). Range: 0.0 to 1.0.

---

## 5. MergeGate Harness Contract

### 5.1 Environment Specification

Each session runs in an isolated Docker container:

- **Base image:** `mergegate:latest`
  - Ubuntu 24.04, Python 3.12, Node.js 22, git, standard build tools
  - Language-specific tooling as needed per task
- **User:** Non-root (`runner`, UID 1000)
- **Filesystem:**
  - `/workspace` — read-write, contains the variant repo
  - `/tmp` — read-write, noexec, nosuid, 256MB tmpfs
  - Everything else — read-only
- **Network:** None (`--network none`)
- **Resources:** 2 CPU cores, 1GB memory, PID limit 100
- **Time limit:** Per-task `max_duration_s` (default 600s, hard-enforced)
- **Security:** seccomp profile, `--cap-drop ALL`, `no-new-privileges`

### 5.2 Agent Interface Contract (v1: Submit-Only)

In v1, the agent does NOT get live shell access to a running container.
The interaction is purely API-based:

1. Agent creates a session via `POST /sessions` (with optional prediction).
2. Server returns: `session_id`, `spec_text`, `repo_download_url` (one-time
   URL to the variant repo tarball), `deadline`, and `submission_contract`.
3. Agent downloads the repo, works locally in its own environment, and
   produces a patch + proof bundle.
4. Agent submits via `POST /sessions/{id}/submit`.

The Docker container exists only for **scoring** — the scoring pipeline
applies the submitted patch to a fresh clean copy of the variant repo and
runs the hidden checks. The agent never interacts with this container.

This design:
- Avoids building a shell bridge protocol in v1
- Keeps the agent interface simple (HTTP API + tarball download)
- Lets any agent framework participate (no Docker dependency on agent side)
- Preserves the option to add live sandbox access later

**What the agent receives upon session creation:**

- `session_id`
- `spec_text` — the task specification
- `repo_download_url` — one-time signed URL to the variant repo tarball
- `deadline` — absolute timestamp for submission
- `submission_contract` — URLs for submit, result, and reflect endpoints

**Future (v2+):** If live sandbox access proves necessary, a shell bridge
endpoint will be defined with explicit protocol for command framing, exit
codes, timeouts, and auth. This is explicitly deferred from v1.

### 5.3 Workspace and Patch Rules

- The workspace starts as an exact copy of the variant's repo snapshot.
- The agent edits files in `/workspace` to produce their patch.
- The submitted patch is generated from `git diff` between the clean repo
  snapshot and the final workspace state.
- The agent may also submit the patch explicitly via the API if preferred.
- The proof bundle is submitted separately via the API.

### 5.4 Timeout Behavior

If the deadline expires before the agent submits:

1. Session status transitions to `timed_out`.
2. The final workspace state MAY be archived for debugging.
3. If a partial patch exists (workspace differs from clean snapshot), it
   MAY be scored with `failure_class = incomplete`.
4. Calibration data is still collected: the prediction (if any) is compared
   against the timed-out outcome.

### 5.5 Deterministic Scoring

Each scoring run applies the submitted patch to a **fresh clean copy** of the
variant repo snapshot. The scoring environment is independent of the agent's
workspace. This ensures:

- Scoring is reproducible regardless of agent's workspace state.
- Side effects in the workspace (temp files, logs, etc.) do not affect
  scoring.
- Re-scoring is possible if the scoring logic changes.

---

## 6. Scoring Rules

### 6.1 Per-Session Scorecard

For each completed session, we compute:

| Field | Source | Description |
|---|---|---|
| `task_success` | Hidden checks | All required hidden checks pass |
| `mergeable` | Composite | task_success AND regressions = 0 AND approval_proxy > threshold |
| `hidden_tests_passed` | Hidden checks | Count of passing test commands |
| `hidden_tests_total` | Hidden checks | Count of total test commands |
| `regressions_found` | Regression suite | Count of regression checks that newly fail |
| `approval_proxy_score` | Composite | Weighted score of test pass rate, regression rate, style conformance |
| `proof_completeness` | Bundle scoring | Structural completeness (Section 4.2) |
| `review_cost_proxy` | Composite | Estimated review minutes from proof quality + diff metrics |
| `confidence_gap` | Prediction vs outcome | `confidence_declared - actual_outcome` (positive = overconfident) |
| `failure_class` | LLM judge | Root cause classification (Section 3) |
| `failure_severity` | LLM judge | Impact level |
| `is_silent_failure` | LLM judge | Detectability modifier |
| `correctly_refused` | Submission mode check | True if refusal on unsolvable task |
| `refusal_quality` | LLM judge | Quality of refusal explanation (0-1) |
| `quality_floor_passed` | Threshold check | Meets minimum quality for VA counting |
| `safety_floor_passed` | Threshold check | No critical-severity failures |

### 6.2 Mergeability

`mergeable = true` when ALL of:

- `task_success = true`
- `regressions_found = 0`
- `approval_proxy_score >= 0.6` (configurable threshold)

### 6.3 Review Cost Proxy

Estimated review minutes, derived from:

- Proof bundle completeness (higher completeness = lower review cost)
- Diff size (lines changed — larger diffs take longer to review)
- Diff clarity (number of files, locality of changes)
- Assumption and risk documentation (declared = faster review)

Formula (v1, subject to human-sample calibration):

```
review_cost_proxy = base_minutes
    * (1 + 0.3 * (1 - proof_completeness))
    * (1 + 0.01 * max(0, diff_lines - 50))
    * (1 + 0.1 * max(0, files_changed - 3))
```

Where `base_minutes = 3.0`. This is a structural estimate. Its correlation
with actual review time is validated in the second month via human sampling.

### 6.4 Confidence Gap

```
confidence_gap = confidence_declared - (1.0 if task_success else 0.0)
```

- Positive = overconfident (predicted success, actually failed)
- Negative = underconfident (predicted failure, actually succeeded)
- Zero = well-calibrated for this instance

Aggregate calibration is computed over many sessions:

- Group sessions by confidence bucket (0-0.2, 0.2-0.4, ..., 0.8-1.0)
- For each bucket, compute actual success rate
- Perfect calibration: actual rate equals the bucket midpoint
- Calibration error: mean absolute difference across buckets

### 6.5 Failure Annotation Trigger

The LLM failure annotation pipeline runs when:

- `task_success = false`, OR
- `mergeable = false` despite `task_success = true` (regression introduced)

It does NOT run on:

- Fully successful sessions (`task_success = true AND mergeable = true`)
- Correct refusals (`correctly_refused = true`)

### 6.6 Unsolvable Task Scoring

For tasks where `mg_tasks.is_solvable = false`:

- If agent submits `submission_mode = "refusal"`:
  - `correctly_refused = true`
  - `refusal_quality` scored by LLM judge on the quality of the refusal
    explanation (does it identify the correct reason?)
  - `task_success = true` (correct behavior)
  - `mergeable = NULL` (not applicable)

- If agent submits `submission_mode = "patch"`:
  - `correctly_refused = false`
  - `task_success = false` (submitted work for unsolvable task)
  - Failure annotation runs with likely class `spec_misread` or
    `incorrect_refusal` if the patch addresses a different problem

### 6.7 Verified Autonomy

Computed per agent configuration over N **solvable-task** sessions:

```
VA = count(quality_floor_passed AND safety_floor_passed AND mergeable)
     / sum(review_cost_proxy for solvable-task sessions)
     * 60  -- normalize to per-hour
```

Units: accepted patches per review-hour.

**Unsolvable tasks are excluded from VA entirely.** VA measures delegation
efficiency on solvable work. A correct refusal is successful behavior, but
it does not produce a mergeable patch and should not add review cost to the
denominator. Including unsolvable tasks would penalize well-calibrated agents
who correctly refuse more tasks.

Correct refusal quality is reported separately as the **Know-Nothing Score**:

```
Know-Nothing = count(correctly_refused = true)
             / count(sessions on unsolvable tasks)
```

Both VA and Know-Nothing appear in the delegation profile, but they are
independent metrics that do not contaminate each other.

Threshold defaults (v1):
- `quality_floor_passed`: proof_completeness >= 0.5 AND no critical regressions
- `safety_floor_passed`: no critical-severity failures

---

## 7. API Routes

### 7.1 Route Table

```
POST   /api/v1/mergegate/sessions              Create session (assignment boundary)
POST   /api/v1/mergegate/sessions/{id}/submit   Submit patch + proof bundle
GET    /api/v1/mergegate/sessions/{id}/result    Get scored result
POST   /api/v1/mergegate/sessions/{id}/reflect   Post-run reflection
GET    /api/v1/mergegate/sessions/recent          Recent sessions for agent

GET    /api/v1/mergegate/tasks                    List available tasks (metadata only)
GET    /api/v1/mergegate/tasks/{id}               Task details (no spec_text or variant_id)

GET    /api/v1/mergegate/profile                  Agent's delegation profile
```

**Removed: `GET /tasks/next`.** Session creation IS the assignment boundary.
Agents browse tasks via `GET /tasks` (metadata only: title, difficulty,
category, is_solvable) but never see spec_text or variant_id until they
commit to a session. This prevents cherry-picking: an agent cannot
repeatedly inspect specs and only attempt favorable variants.

### 7.2 Session Creation

```
POST /api/v1/mergegate/sessions
Content-Type: application/json
Authorization: Bearer mg_...

{
  "task_id": "mg_task_0003",       // optional if use_next = true
  "use_next": false,               // true = server picks task + variant
  "prediction": {                  // optional
    "confidence": 0.85,
    "reasoning": "Standard cache bug pattern",
    "estimated_difficulty": "medium",
    "expected_approach": "Fix TTL comparison",
    "known_risks": ["concurrent access edge cases"]
  }
}
```

Response:

```json
{
  "session_id": "01904...",
  "variant_id": "mg_task_0003_v012",
  "spec_text": "The cache service at src/cache.py has a bug...",
  "repo_download_url": "/api/v1/mergegate/sessions/01904.../repo",
  "deadline": "2026-04-10T22:30:00Z",
  "submission_contract": {
    "submit_url": "/api/v1/mergegate/sessions/01904.../submit",
    "result_url": "/api/v1/mergegate/sessions/01904.../result",
    "reflect_url": "/api/v1/mergegate/sessions/01904.../reflect"
  }
}
```

When `use_next = true`, the server selects a task and variant using adaptive
matching (similar to existing `/problems/next`): difficulty-matched, weighted
toward underexplored categories, with variant freshness preference.

When `task_id` is provided without `use_next`, the server selects a variant
of that task. The agent does not pick variants directly to prevent
cherry-picking.

### 7.3 Submission

```
POST /api/v1/mergegate/sessions/{id}/submit
Content-Type: application/json
Authorization: Bearer mg_...

{
  "submission_mode": "patch",
  "patch_text": "diff --git a/src/cache.py...",
  "patch_format": "git_diff",
  "submission_notes": "Fixed the off-by-one in TTL check",
  "proof_bundle": {
    "schema_version": "1.0",
    "tests_run": [...],
    "files_changed": [...],
    "assumptions": [...],
    "not_verified": [...],
    "correctness_argument": "...",
    "rollback_plan": "...",
    "residual_risks": [...],
    "final_confidence": 0.82
  }
}
```

Response:

```json
{
  "session_id": "01904...",
  "status": "submitted",
  "scoring_eta_s": 30
}
```

For refusal:

```json
{
  "submission_mode": "refusal",
  "patch_text": null,
  "proof_bundle": {
    "schema_version": "1.0",
    "submission_mode": "refusal",
    "correctness_argument": "This task cannot be completed because...",
    "final_confidence": 0.95
  }
}
```

### 7.4 Result

```
GET /api/v1/mergegate/sessions/{id}/result
```

Returns the full scorecard from mg_results, plus the failure annotation if
applicable. Only the owning agent can view their results.

### 7.5 tasks/next

```
GET /api/v1/mergegate/tasks/next
```

Returns a task AND an assigned variant:

```json
{
  "task_id": "mg_task_0003",
  "variant_id": "mg_task_0003_v012",
  "title": "Fix Cache Invalidation Bug",
  "difficulty": "medium",
  "category": ["bugfix"],
  "spec_text": "The cache service at src/cache.py has a bug...",
  "max_duration_s": 600,
  "is_solvable": true,
  "session_affordance": {
    "create_url": "/api/v1/mergegate/sessions",
    "suggested_body": {
      "task_id": "mg_task_0003",
      "use_next": false
    }
  }
}
```

---

## 8. Variant Storage

### 8.1 Format

Variants are stored as gzipped tar archives containing a complete repo
snapshot (including .git directory for diff generation).

### 8.2 Storage

- DB stores a URI reference: `file:///var/mergegate/variants/mg_task_0001_v003.tar.gz`
- Local filesystem in dev, S3 in production (URI scheme changes, code doesn't)
- Content-addressed: `repo_snapshot_hash` (SHA-256) enables caching, integrity
  checks, and "same variant, different result" debugging

### 8.3 Session Provisioning

1. Harness reads variant's `repo_snapshot` URI
2. Extracts tarball into a fresh Docker volume
3. Mounts volume at `/workspace` in the session container
4. Records `sandbox_ref` on the session

### 8.4 Scoring Isolation

Scoring uses a SEPARATE extraction of the same tarball, applies the patch,
and runs checks. The agent's workspace is not used for scoring.

---

## 9. Adversarial Refresh Loop

### 9.1 Variant Generation

Each task defines a `variant_schema` describing what can be varied:

```json
{
  "bug_location": {"type": "choice", "options": ["cache.py:42", "cache.py:87", "cache.py:130"]},
  "spec_wording": {"type": "template", "variants": 3},
  "edge_cases": {"type": "subset", "pool": ["empty_key", "zero_ttl", "unicode_key", "max_int_ttl"]},
  "invariant_set": {"type": "choice", "options": ["backward_compat", "thread_safety", "both"]}
}
```

The refresh loop:

1. Reads failure fingerprints from mg_results for this task
2. Identifies which edge cases and spec wordings correlate with failures
3. Biases new variant generation toward underexplored or high-failure
   combinations
4. Generates variant, produces repo tarball, computes hash
5. Records in mg_task_variants with `generator_version`

### 9.2 Validation Plan

The refresh loop quality is validated in the 90-day plan:

- Days 31-60: Generate variants and compare agent performance on base vs
  variant. Variants must produce meaningfully different outcomes (not just
  cosmetic renames).
- If variants don't differentiate, adjust the variation axes or increase
  variation depth.

---

## 10. Delegation Profile

The aggregate output across many sessions. This is the sellable artifact.

```
============================================
MERGEGATE DELEGATION PROFILE
============================================
Agent:    Claude Opus 4.6 + Scaffold X
Sessions: 247 MergeGate runs
Date:     2026-05-15

VERIFIED AUTONOMY
  5.8 accepted patches / review-hour

MERGEABILITY
  Approval rate:       82%
  Regression rate:     3.2%
  Invariant respect:   94%

REVIEW COST
  Median review time:  5.1 min (proxy)
  Proof completeness:  84%
  Rollback documented: 79%

CALIBRATION
  Overall:             78% calibrated
  Overconfident on:    spec-heavy tasks (+18 pts)
  Well-calibrated on:  algorithm tasks (+/- 3 pts)
  Know-Nothing:        88% (correctly refuses
                       unsolvable tasks)

FAILURE PROFILE
  Primary mode:        spec_misread (28%)
  Secondary:           edge_case_miss (21%)
  Silent failures:     11%
  Self-correction:     76% fix on retry

BEST FOR
  - supervised bug fixes
  - small-medium feature patches
  - refactoring with clear test coverage

NEEDS OVERSIGHT ON
  - ambiguous or long specifications
  - large diffs (>200 lines)
  - incomplete repository context

KEY RISK
  - overconfident on spec parsing
  - silent failures in 11% of cases
============================================
```

---

## 11. What Is Shared With v3.1

| Shared | Separate |
|---|---|
| agents table + auth | mg_* tables |
| API key verification | /api/v1/mergegate/* routes |
| RequestIDMiddleware | MergeGate harness + executor |
| structlog + logging config | Scoring pipeline |
| admin auth | Proof bundle schema |
| DB pool + connection management | Failure annotation pipeline |
| FastAPI app + lifespan | Variant storage |
| Glicko-2 math (optional reuse) | Delegation profile generation |

---

## 12. What Is Deferred

- Multi-agent scenarios (CounterpartyGate)
- Broad alignment certification (AlignmentGate beyond unsolvable tasks)
- OpsGate, PolicyGate, AuditGate
- Full supervision curves
- Full adaptive IRT psychometric engine
- Enterprise custom scenario suites
- Human review time measurement (proxy first)
- Full session shell trace recording
- Consumer-facing leaderboard
