# Security Policy

## Reporting a Vulnerability

If you believe you've found a security issue in MergeGate — including in the
scoring pipeline (which runs `git apply` and executes subprocess commands
against task check scripts), the API key handling, or anywhere else — please
report it privately rather than opening a public issue.

**Contact:** benlamm25@gmail.com

Please include:
- A description of the issue and its impact
- Steps to reproduce (minimal PoC preferred)
- Any relevant code paths or file references

You can expect an acknowledgement within a few days. This is a shelved
research project maintained by a single person, so response times are
best-effort — but security reports will be triaged ahead of feature work.

## Scope

In scope:
- The FastAPI server (`api/`) and its authentication, authorization, and
  input validation
- The MergeGate scoring pipeline (`api/services/mg_scorer.py`,
  `scripts/run_offline.py`) — patch application, subprocess execution,
  temp-dir handling
- The task seeding pipeline (`scripts/seed_mergegate_tasks.py`) and the
  variant tarball handling

Out of scope:
- The legacy v1–v3 competitive-programming schema (migrations 0001–0015) —
  this code is retained for historical continuity only, not actively
  maintained
- Vulnerabilities in upstream dependencies (report those to the upstream
  project; if MergeGate's usage of a dependency makes the impact worse
  than typical, that *is* in scope)

## Not a Production System

MergeGate is a research benchmark. Do not run it as a public-facing service
without significant additional hardening. Specifically:
- The scoring pipeline applies arbitrary agent-submitted patches and runs
  arbitrary check commands in subprocesses — anyone running this as a
  hosted service should sandbox the scorer (containers, VMs, or similar)
- The default `.env.example` credentials are for local development only
