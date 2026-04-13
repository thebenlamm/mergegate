# MergeGate

Delegation benchmark for AI coding agents. Measures whether an agent knows when to refuse — paired solvable/unsolvable twins, Youden's J as the headline calibration metric.

Read `README.md` for the pilot finding. Read `docs/plans/2026-04-10-mergegate-design.md` for the full design doc.

## Stack

- **API:** FastAPI (Python 3.12), async everywhere, uvicorn
- **Database:** PostgreSQL 16 with asyncpg (raw SQL, no ORM)
- **Migrations:** Alembic (async template, `postgresql+asyncpg://` URL)
- **Scoring:** Subprocess-based patch application + test execution (no Docker)
- **Auth:** API keys (bcrypt-hashed), Bearer token on all endpoints except registration
- **Testing:** pytest + pytest-asyncio, httpx AsyncClient with ASGITransport

## Project Structure

```
mergegate/
├── CLAUDE.md                     # This file — dev guide
├── README.md                     # Pilot finding + reproduction steps
├── api/
│   ├── main.py                   # FastAPI app, lifespan, middleware, error handlers
│   ├── config.py                 # pydantic-settings, all config from env
│   ├── deps.py                   # Dependency injection (DB pool, auth)
│   ├── errors.py                 # AppError class, global exception handlers
│   ├── logging_config.py         # structlog setup, RequestIDMiddleware (pure ASGI)
│   ├── utils.py                  # UUID7 generation, rating display helpers
│   ├── models/
│   │   ├── agents.py             # RegisterRequest, ProfileResponse
│   │   ├── common.py             # PaginatedResponse[T], ErrorResponse
│   │   └── mergegate.py          # MergeGate request/response models
│   ├── routes/
│   │   ├── agents.py             # POST /register, GET /me
│   │   ├── mergegate.py          # MergeGate: tasks, sessions, submit, result, profile
│   │   ├── health.py             # GET /health
│   │   └── skill_files.py        # GET /skill.md, /heartbeat.md, /compete.md
│   └── services/
│       ├── auth.py               # API key gen (mg_...), bcrypt hash, TTL cache
│       ├── mg_scorer.py          # Scoring pipeline (patch + checks + results)
│       └── proof_scoring.py      # Proof bundle completeness + review cost estimation
├── tasks/                        # MergeGate task repos
│   ├── mg_task_0001_cache_ttl/   # Easy bugfix: TTL boundary condition
│   │   ├── task.json             # Manifest: id, spec_text, checks, is_solvable
│   │   ├── repo/                 # Python project with the planted bug
│   │   └── solution.patch        # Reference fix (used by --verify only)
│   └── ...
├── db/
│   ├── schema.sql                # Reference DDL
│   └── migrations/               # Alembic async migrations
├── tests/                        # pytest suites
├── scripts/
│   ├── run_offline.py            # Primary runner — no DB, no API server
│   ├── analyze_results.py        # Aggregate runs into comparison tables
│   ├── run_agent.py              # LLM call helpers (shared with run_offline)
│   ├── seed_mergegate_tasks.py   # Seed task repos into DB (--verify, --clean)
│   ├── generate_profile.py       # Generate delegation profiles (--compare)
│   └── demo_mergegate.sh         # One-command end-to-end demo
├── docker-compose.yml            # PostgreSQL 16 (dev + test)
├── pyproject.toml                # Dependencies, pytest, ruff, coverage config
├── alembic.ini                   # Alembic config
└── .env.example                  # DATABASE_URL, TEST_DATABASE_URL, LOG_FORMAT, LOG_LEVEL
```

## Conventions

### Python
- Type hints everywhere, async everywhere
- No ORM — raw SQL via asyncpg with parameterized queries
- Pydantic v2 for all request/response validation
- Errors return `{"error": "message", "code": "ERROR_CODE"}` with appropriate HTTP status
- UUID7 for primary keys
- Structured logging (structlog, JSON output) with request IDs

### API Design
- All routes return JSON; no HTML rendering
- Pagination: `limit` + `offset` query params, response includes `total` count
- All endpoints under `/api/v1/` prefix
- MergeGate endpoints under `/api/v1/mergegate/`

### Database
- Alembic for all schema changes — never modify schema.sql directly, generate migration first
- schema.sql is reference only, kept in sync manually after migrations
- All timestamps are `TIMESTAMPTZ`, stored as UTC
- JSONB for task checks, proof bundles, failure details

### MergeGate Scoring Pipeline
- `mg_scorer.py` is the core — applies patches, runs checks, writes mg_results
- Scoring runs as a FastAPI background task, triggered by the submit endpoint (hosted mode),
  or inline via `scripts/run_offline.py` (offline mode — the primary reproduction path)
- Patches applied via `git apply` in a temp directory (subprocess, not Docker)
- Check commands from `resolved_checks` JSONB executed via `bash -c` subprocess
- Failure classification is heuristic (not LLM-based): patch_failed, tests_failed, regression, timeout, incorrect_refusal
- `proof_scoring.py` computes structural completeness (field presence, not quality) and review cost estimate
- Session lifecycle: pending → running → scoring → completed (or error/timed_out)

### MergeGate Task Repos
- Each task in `tasks/mg_task_NNNN_name/` with `task.json`, `repo/`, `solution.patch`
- `task.json` defines id, title, difficulty, category, spec_text, resolved_checks, is_solvable, twin_group
- `repo/` is a Python project with a planted bug (git-inited at seed time in a tarball copy)
- `solution.patch` is the reference fix — verified by `seed_mergegate_tasks.py --verify`
- For the hosted server, repos are tarballed at seed time and stored in `var/mergegate/variants/`
- The offline runner reads `tasks/` directly — no DB or seeding required

### Security
- API keys generated server-side, hashed with bcrypt (rounds=12), never stored plaintext
- In-process TTL cache (60s) on key verification to avoid bcrypt saturation
- Submission patches validated (size < 64KB) before scoring

### Git
- Atomic commits per logical change
- Format: `type(scope): description`
- Types: feat, fix, refactor, docs, test, infra
- Scopes: api, db, tests, docs, scripts, tasks

### Testing
- Every route gets integration tests with httpx AsyncClient + ASGITransport
- Use `ASGITransport(raise_app_exceptions=False)` to receive HTTP responses through exception handlers
- conftest.py provides async client fixture with mocked asyncpg pool

### Key Patterns
- Pure ASGI middleware for RequestIDMiddleware (not BaseHTTPMiddleware — it breaks exception handler chain)
- Health endpoint acquires from pool directly (not `Depends(get_db)`) for graceful degradation

## Build & Test

```bash
# Install dependencies
pip install -e ".[dev]"

# Offline mode — primary reproduction path (no DB, no API server)
python scripts/run_offline.py --model claude-sonnet-4-20250514 --provider anthropic
python scripts/analyze_results.py

# Hosted mode (optional — FastAPI server + PostgreSQL)
docker compose up -d
alembic upgrade head
python scripts/seed_mergegate_tasks.py --verify
uvicorn api.main:app --reload

# Run tests (no DB needed for most)
python -m pytest tests/ --ignore=tests/test_db.py --ignore=tests/test_migrations.py -q

# Full suite (requires running PostgreSQL)
python -m pytest tests/ -q

# Lint + format
ruff check .
ruff format --check .

# Coverage
coverage run -m pytest tests/ && coverage report
```

## Do Not

- Do not expose hidden test cases or expected outputs in any agent-facing API response
- Do not use an ORM — asyncpg with raw SQL only
- Do not store API keys in plaintext anywhere
- Do not use BaseHTTPMiddleware — use pure ASGI class instead
