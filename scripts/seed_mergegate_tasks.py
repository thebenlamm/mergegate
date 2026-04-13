#!/usr/bin/env python3
"""Seed MergeGate tasks into the database from tasks/ directory.

Reads task.json manifests, creates .tar.gz tarballs of repos,
computes SHA-256 hashes, and inserts mg_tasks + mg_task_variants rows.

Usage:
    python scripts/seed_mergegate_tasks.py [--verify] [--clean]

Options:
    --verify    Apply solution.patch to each repo and run checks to verify
    --clean     Delete existing MergeGate tasks before seeding
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Project root
ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"
VARIANTS_DIR = ROOT / "var" / "mergegate" / "variants"


def _git_init_repo(repo_dir: Path) -> None:
    """Initialize a git repo and make an initial commit."""
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "seed",
            "GIT_AUTHOR_EMAIL": "seed@mergegate.local",
            "GIT_COMMITTER_NAME": "seed",
            "GIT_COMMITTER_EMAIL": "seed@mergegate.local",
        },
    )


def create_tarball(repo_dir: Path, output_path: Path) -> str:
    """Create a .tar.gz of the repo directory and return its SHA-256 hash.

    Copies repo to a temp dir, git-inits it (so git apply works downstream),
    then tars the result.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_repo = Path(tmp) / repo_dir.name
        shutil.copytree(repo_dir, tmp_repo)
        _git_init_repo(tmp_repo)

        with tarfile.open(output_path, "w:gz") as tar:
            tar.add(tmp_repo, arcname=repo_dir.name)

    sha256 = hashlib.sha256()
    with open(output_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_witness(task_dir: Path) -> bool:
    """Apply solution.patch and run tests to verify the task is solvable."""
    solution_patch = task_dir / "solution.patch"
    repo_dir = task_dir / "repo"

    if not solution_patch.exists():
        print("  No solution.patch found — skipping verification")
        return True

    # Check if patch is empty (unsolvable task)
    patch_content = solution_patch.read_text().strip()
    if not patch_content:
        print("  Empty solution.patch (unsolvable task) — skipping verification")
        return True

    # Work in a temp copy with git init (git apply requires a repo)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_repo = Path(tmp) / "repo"
        shutil.copytree(repo_dir, tmp_repo)
        _git_init_repo(tmp_repo)

        # Apply patch
        result = subprocess.run(
            ["git", "apply", str(solution_patch)],
            cwd=tmp_repo,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  FAIL: solution.patch did not apply: {result.stderr}")
            return False

        # Run tests
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
            cwd=tmp_repo,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("  FAIL: tests did not pass after applying solution:")
            print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
            return False

        print("  PASS: solution verified")
        return True


async def seed_task(db, task_dir: Path, verify: bool = False) -> bool:
    """Seed a single task from its directory."""
    task_json = task_dir / "task.json"
    if not task_json.exists():
        print(f"Skipping {task_dir.name}: no task.json")
        return False

    manifest = json.loads(task_json.read_text())
    task_id = manifest["id"]
    print(f"\nSeeding {task_id}: {manifest['title']}")

    # Verify witness if requested
    if verify:
        if not verify_witness(task_dir):
            print("  SKIPPING: witness verification failed")
            return False

    # Create tarball
    VARIANTS_DIR.mkdir(parents=True, exist_ok=True)
    repo_dir = task_dir / "repo"
    variant = manifest["variant"]
    variant_id = f"{task_id}_v{variant['seed']:03d}"
    tarball_path = VARIANTS_DIR / f"{variant_id}.tar.gz"
    repo_hash = create_tarball(repo_dir, tarball_path)
    print(f"  Created tarball: {tarball_path} ({repo_hash[:12]}...)")

    # Check if task already exists
    existing = await db.fetchval("SELECT id FROM mg_tasks WHERE id = $1", task_id)
    if existing:
        print(f"  Task {task_id} already exists — updating")
        await db.execute("DELETE FROM mg_task_variants WHERE task_id = $1", task_id)
        await db.execute("DELETE FROM mg_tasks WHERE id = $1", task_id)

    # Insert mg_tasks
    await db.execute(
        """
        INSERT INTO mg_tasks (
            id, title, description, difficulty, category,
            repo_source, base_checks, scoring_config,
            is_solvable, unsolvable_reason, max_duration_s, is_active
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, TRUE)
        """,
        task_id,
        manifest["title"],
        manifest["description"],
        manifest["difficulty"],
        manifest["category"],
        f"file://{repo_dir}",
        json.dumps(manifest["base_checks"]),
        json.dumps(manifest.get("scoring_config", {})),
        manifest["is_solvable"],
        manifest.get("unsolvable_reason"),
        manifest.get("max_duration_s", 600),
    )

    # Compute spec_text hash
    spec_text = variant["spec_text"]
    spec_hash = hashlib.sha256(spec_text.encode("utf-8")).hexdigest()

    # Insert mg_task_variants
    await db.execute(
        """
        INSERT INTO mg_task_variants (
            id, task_id, variant_params, repo_snapshot, repo_snapshot_hash,
            resolved_checks, spec_text, spec_hash, seed, generator_version,
            is_active
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, TRUE)
        """,
        variant_id,
        task_id,
        json.dumps({"seed": variant["seed"]}),
        str(tarball_path),
        repo_hash,
        json.dumps(variant["resolved_checks"]),
        spec_text,
        spec_hash,
        variant["seed"],
        "manual_v1",
    )

    print(f"  Inserted task {task_id} + variant {variant_id}")
    return True


async def main():
    parser = argparse.ArgumentParser(description="Seed MergeGate tasks")
    parser.add_argument("--verify", action="store_true", help="Verify solution patches")
    parser.add_argument("--clean", action="store_true", help="Clean existing tasks first")
    parser.add_argument("--db-url", default=None, help="Database URL")
    args = parser.parse_args()

    db_url = args.db_url or os.environ.get("DATABASE_URL", "postgresql://localhost/mergegate")

    conn = await asyncpg.connect(db_url)

    try:
        if args.clean:
            print("Cleaning existing MergeGate tasks...")
            await conn.execute("DELETE FROM mg_session_events")
            await conn.execute("DELETE FROM mg_reflections")
            await conn.execute("DELETE FROM mg_results")
            await conn.execute("DELETE FROM mg_proof_bundles")
            await conn.execute("DELETE FROM mg_submissions")
            await conn.execute("DELETE FROM mg_predictions")
            await conn.execute("DELETE FROM mg_sessions")
            await conn.execute("DELETE FROM mg_task_variants")
            await conn.execute("DELETE FROM mg_tasks")
            print("  Done")

        # Find all task directories
        task_dirs = sorted(
            d for d in TASKS_DIR.iterdir() if d.is_dir() and d.name.startswith("mg_task_")
        )

        if not task_dirs:
            print("No task directories found in tasks/")
            sys.exit(1)

        print(f"Found {len(task_dirs)} task(s)")

        success = 0
        for task_dir in task_dirs:
            if await seed_task(conn, task_dir, verify=args.verify):
                success += 1

        print(f"\nSeeded {success}/{len(task_dirs)} tasks successfully")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
