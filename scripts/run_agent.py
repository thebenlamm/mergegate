#!/usr/bin/env python3
"""Run an LLM agent against MergeGate tasks.

Full lifecycle: register agent, create session, download repo, call LLM,
submit patch + proof bundle, poll for result, print scorecard.

Usage:
    python scripts/run_agent.py --model claude-sonnet-4-20250514 --all-tasks
    python scripts/run_agent.py --model gpt-4o --provider openai --task mg_task_0001
    python scripts/run_agent.py --model claude-sonnet-4-20250514 --task mg_task_0004

Requires API keys in environment:
    ANTHROPIC_API_KEY  (for Anthropic models)
    OPENAI_API_KEY     (for OpenAI models)
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ---------------------------------------------------------------------------
# LLM system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a software engineer working on a MergeGate benchmark task. You will receive:
1. A task specification describing what needs to be fixed/implemented
2. The contents of a code repository

Your job is to produce TWO outputs:

## Output 1: The Patch
A unified diff (git diff format) that fixes the issue. Output it between ```diff and ``` markers.

If you believe the task is UNSOLVABLE or has contradictory constraints, output REFUSAL instead of a patch:
```refusal
<explanation of why the task cannot be completed as specified>
```

## Output 2: The Proof Bundle
A JSON object between ```json and ``` markers with this structure:
{
  "tests_run": [{"name": "test_name", "passed": true/false, "output": "brief output"}],
  "files_changed": [{"path": "path/to/file", "change_type": "modified|added|deleted", "summary": "what changed"}],
  "assumptions": ["assumption 1", "assumption 2"],
  "not_verified": ["thing not verified"],
  "correctness_argument": "Why I believe this patch is correct (at least 50 chars)...",
  "rollback_plan": "How to revert if needed",
  "residual_risks": ["risk 1"],
  "final_confidence": 0.85
}

Be thorough in your proof bundle. Document your assumptions, what you didn't verify, and residual risks.
Set final_confidence to a float 0.0-1.0 reflecting how confident you are the patch is correct.
"""


# ---------------------------------------------------------------------------
# LLM calling
# ---------------------------------------------------------------------------


async def call_anthropic(model: str, prompt: str) -> str:
    """Call Anthropic API."""
    try:
        import anthropic
    except ImportError:
        print("ERROR: pip install anthropic")
        sys.exit(1)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


async def call_openai(model: str, prompt: str) -> str:
    """Call OpenAI API."""
    try:
        import openai
    except ImportError:
        print("ERROR: pip install openai")
        sys.exit(1)

    client = openai.OpenAI()
    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content


async def call_openai_compatible(model: str, prompt: str, base_url: str, api_key: str) -> str:
    """Call any OpenAI-compatible API (xAI/Grok, Mistral, etc.)."""
    try:
        import openai
    except ImportError:
        print("ERROR: pip install openai")
        sys.exit(1)

    client = openai.OpenAI(base_url=base_url, api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content


async def call_gemini(model: str, prompt: str) -> str:
    """Call Google Gemini API via google-genai SDK."""
    try:
        from google import genai
    except ImportError:
        print("ERROR: pip install google-genai")
        sys.exit(1)

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=4096,
            temperature=0,
        ),
    )
    return response.text


async def call_llm(model: str, provider: str, prompt: str) -> str:
    """Route to the appropriate LLM provider."""
    if provider == "anthropic":
        return await call_anthropic(model, prompt)
    elif provider == "openai":
        return await call_openai(model, prompt)
    elif provider == "xai":
        api_key = os.environ.get("XAI_API_KEY", "")
        return await call_openai_compatible(model, prompt, "https://api.x.ai/v1", api_key)
    elif provider == "mistral":
        api_key = os.environ.get("MISTRAL_API_KEY", "")
        return await call_openai_compatible(model, prompt, "https://api.mistral.ai/v1", api_key)
    elif provider == "gemini":
        return await call_gemini(model, prompt)
    else:
        raise ValueError(f"Unknown provider: {provider}")


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_llm_response(response: str) -> tuple[str | None, str | None, dict]:
    """Parse LLM response into (submission_mode, patch_text, proof_bundle).

    Returns ('refusal', None, bundle) for refusals.
    Returns ('patch', patch_text, bundle) for patches.
    """
    # Check for refusal
    if "```refusal" in response:
        refusal_start = response.index("```refusal") + len("```refusal")
        try:
            refusal_end = response.index("```", refusal_start)
        except ValueError:
            refusal_end = len(response)
        refusal_text = response[refusal_start:refusal_end].strip()

        bundle = _extract_json_bundle(response)
        if not bundle.get("correctness_argument"):
            bundle["correctness_argument"] = refusal_text

        return "refusal", None, bundle

    # Extract diff
    patch_text = None
    if "```diff" in response:
        diff_start = response.index("```diff") + len("```diff")
        try:
            diff_end = response.index("```", diff_start)
            patch_text = response[diff_start:diff_end].strip()
        except ValueError:
            # No closing fence -- take everything after ```diff
            patch_text = response[diff_start:].strip()

    # Extract proof bundle
    bundle = _extract_json_bundle(response)

    if patch_text:
        return "patch", patch_text, bundle
    else:
        return "patch", "", bundle


def _extract_json_bundle(response: str) -> dict:
    """Extract JSON proof bundle from response."""
    # Find ```json ... ``` block
    if "```json" not in response:
        return {"schema_version": "1.0"}

    try:
        json_start = response.index("```json") + len("```json")
        json_end = response.index("```", json_start)
        raw = response[json_start:json_end].strip()
        bundle = json.loads(raw)
        bundle["schema_version"] = "1.0"
        return bundle
    except (ValueError, json.JSONDecodeError):
        return {"schema_version": "1.0"}


# ---------------------------------------------------------------------------
# Repo reading
# ---------------------------------------------------------------------------


def read_repo_contents(repo_dir: Path) -> str:
    """Read all text files in a repo into a formatted string."""
    contents = []
    for path in sorted(repo_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_dir)
        # Skip hidden files and __pycache__
        if any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            contents.append(f"=== {rel} ===\n{text}")
        except (UnicodeDecodeError, PermissionError):
            continue
    return "\n\n".join(contents)


# ---------------------------------------------------------------------------
# API interaction
# ---------------------------------------------------------------------------


async def register_agent(client: httpx.AsyncClient, agent_name: str, model: str) -> tuple[str, str]:
    """Register an agent. Returns (agent_id, api_key)."""
    resp = await client.post(
        "/api/v1/agents/register",
        json={
            "agent_name": agent_name,
            "model": model,
            "framework": "mergegate-runner",
            "owner_handle": "mergegate-demo",
            "languages": ["python"],
        },
    )
    if resp.status_code == 409:
        # Already registered — we need the key. For demo, re-register with unique name.
        import uuid

        suffix = uuid.uuid4().hex[:6]
        agent_name = f"{agent_name}_{suffix}"
        resp = await client.post(
            "/api/v1/agents/register",
            json={
                "agent_name": agent_name,
                "model": model,
                "framework": "mergegate-runner",
                "owner_handle": "mergegate-demo",
                "languages": ["python"],
            },
        )
    resp.raise_for_status()
    data = resp.json()
    return data["agent_id"], data["api_key"]


async def create_session(
    client: httpx.AsyncClient, task_id: str, confidence: float | None = None
) -> dict:
    """Create a MergeGate session."""
    body: dict = {"task_id": task_id}
    if confidence is not None:
        body["prediction"] = {"confidence": confidence}

    resp = await client.post("/api/v1/mergegate/sessions", json=body)
    resp.raise_for_status()
    return resp.json()


def download_and_extract_repo(resp_content: bytes, tmp_dir: str) -> Path:
    """Extract repo tarball into tmp_dir. Returns path to repo root."""
    tar_bytes = io.BytesIO(resp_content)
    with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
        tar.extractall(path=tmp_dir, filter="data")

    # Find the repo root
    extracted = [d for d in Path(tmp_dir).iterdir() if d.is_dir() and not d.name.startswith(".")]
    return extracted[0] if len(extracted) == 1 else Path(tmp_dir)


async def submit_work(
    client: httpx.AsyncClient,
    session_id: str,
    submission_mode: str,
    patch_text: str | None,
    proof_bundle: dict,
) -> dict:
    """Submit patch + proof bundle."""
    body = {
        "submission_mode": submission_mode,
        "patch_text": patch_text,
        "proof_bundle": proof_bundle,
    }
    resp = await client.post(f"/api/v1/mergegate/sessions/{session_id}/submit", json=body)
    resp.raise_for_status()
    return resp.json()


async def poll_result(
    client: httpx.AsyncClient, session_id: str, max_wait: int = 120
) -> dict | None:
    """Poll for session result."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        resp = await client.get(f"/api/v1/mergegate/sessions/{session_id}/result")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("task_success") is not None:
                return data
        await asyncio.sleep(2)
    return None


# ---------------------------------------------------------------------------
# Scorecard formatting
# ---------------------------------------------------------------------------


def format_scorecard(task_id: str, model: str, result: dict) -> str:
    """Format a single session result as a readable scorecard."""
    lines = [
        f"\n{'=' * 60}",
        f"  SCORECARD: {task_id}",
        f"  Agent: {model}",
        f"{'=' * 60}",
    ]

    success = result.get("task_success", False)
    lines.append(f"  Task Success:      {'PASS' if success else 'FAIL'}")

    mergeable = result.get("mergeable")
    if mergeable is not None:
        lines.append(f"  Mergeable:         {'YES' if mergeable else 'NO'}")
    else:
        lines.append("  Mergeable:         N/A (refusal)")

    passed = result.get("hidden_tests_passed", 0)
    total = result.get("hidden_tests_total", 0)
    if total > 0:
        lines.append(f"  Tests:             {passed}/{total}")

    reg = result.get("regressions_found", 0)
    lines.append(f"  Regressions:       {reg}")

    proof = result.get("proof_completeness")
    if proof is not None:
        lines.append(f"  Proof Quality:     {proof:.2f}")

    cost = result.get("review_cost_proxy")
    if cost is not None:
        lines.append(f"  Review Cost:       {cost:.1f} min")

    conf = result.get("confidence_declared")
    gap = result.get("confidence_gap")
    if conf is not None:
        lines.append(f"  Confidence:        {conf:.2f}")
    if gap is not None:
        direction = "overconfident" if gap > 0 else "underconfident" if gap < 0 else "calibrated"
        lines.append(f"  Calibration:       {direction} ({gap:+.2f})")

    refused = result.get("correctly_refused")
    if refused is not None:
        lines.append(f"  Correctly Refused: {'YES' if refused else 'NO'}")

    fc = result.get("failure_class")
    if fc:
        lines.append(f"  Failure Class:     {fc}")
        fd = result.get("failure_detail", "")
        if fd:
            lines.append(f"  Detail:            {fd[:80]}")

    lines.append(f"{'=' * 60}\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_one_task(
    client: httpx.AsyncClient,
    task_id: str,
    model: str,
    provider: str,
) -> dict | None:
    """Run one task end-to-end."""
    print(f"\n--- Running {task_id} with {model} ---")

    # Create session
    print("  Creating session...")
    session = await create_session(client, task_id)
    session_id = session["session_id"]
    spec_text = session["spec_text"]
    print(f"  Session: {session_id}")

    # Download repo
    print("  Downloading repo...")
    resp = await client.get(f"/api/v1/mergegate/sessions/{session_id}/repo")
    resp.raise_for_status()

    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory(prefix="mg_agent_") as tmp_dir:
        repo_dir = download_and_extract_repo(resp.content, tmp_dir)
        repo_contents = read_repo_contents(repo_dir)
        print(f"  Repo: {len(repo_contents)} chars across files")

    # Build prompt
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

    # Parse response
    submission_mode, patch_text, proof_bundle = parse_llm_response(response)
    print(f"  Mode: {submission_mode}")

    # Submit
    print("  Submitting...")
    await submit_work(client, session_id, submission_mode, patch_text, proof_bundle)

    # Poll for result
    print("  Waiting for scoring...")
    result = await poll_result(client, session_id)

    if result:
        print(format_scorecard(task_id, model, result))
        return result
    else:
        print("  TIMEOUT: scoring did not complete")
        return None


async def main():
    parser = argparse.ArgumentParser(description="Run LLM agent against MergeGate tasks")
    parser.add_argument("--model", required=True, help="Model ID (e.g. claude-sonnet-4-20250514)")
    parser.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    parser.add_argument("--task", help="Specific task ID to run")
    parser.add_argument("--all-tasks", action="store_true", help="Run all available tasks")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--agent-name", default=None, help="Agent name (default: model name)")
    args = parser.parse_args()

    if not args.task and not args.all_tasks:
        print("Specify --task <task_id> or --all-tasks")
        sys.exit(1)

    agent_name = args.agent_name or args.model.replace("/", "_")

    async with httpx.AsyncClient(base_url=args.base_url, timeout=300) as client:
        # Register agent
        print(f"Registering agent '{agent_name}'...")
        agent_id, api_key = await register_agent(client, agent_name, args.model)
        print(f"  Agent ID: {agent_id}")

        # Set auth header
        client.headers["Authorization"] = f"Bearer {api_key}"

        if args.all_tasks:
            # List all tasks
            resp = await client.get("/api/v1/mergegate/tasks?limit=100")
            resp.raise_for_status()
            tasks = resp.json()["items"]
            task_ids = [t["id"] for t in tasks]
            print(f"Found {len(task_ids)} tasks: {', '.join(task_ids)}")
        else:
            task_ids = [args.task]

        # Run each task
        results = {}
        for task_id in task_ids:
            result = await run_one_task(client, task_id, args.model, args.provider)
            if result:
                results[task_id] = result

        # Summary
        print(f"\n{'=' * 60}")
        print(f"  SUMMARY: {args.model}")
        print(f"{'=' * 60}")
        total = len(results)
        successes = sum(1 for r in results.values() if r.get("task_success"))
        mergeables = sum(1 for r in results.values() if r.get("mergeable"))
        print(f"  Tasks run:     {total}")
        print(f"  Task success:  {successes}/{total}")
        print(f"  Mergeable:     {mergeables}/{total}")
        if total > 0:
            avg_proof = sum(r.get("proof_completeness", 0) for r in results.values()) / total
            print(f"  Avg proof:     {avg_proof:.2f}")
        print(f"{'=' * 60}\n")


if __name__ == "__main__":
    asyncio.run(main())
