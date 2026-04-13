"""Tests for Alembic migrations (INFR-04, INFR-06).

Runs alembic upgrade head against the docker-compose db-test service
and verifies all expected tables exist with correct column types.

Requires: docker-compose db-test service running on port 5433.
Skip gracefully if db-test is not available or SKIP_DB_TESTS=1.

Uses subprocess.run for alembic (never programmatic invocation inside
async code — see RESEARCH.md Pitfall 1: asyncio.run() in existing loop).
"""

import os
import subprocess

import pytest

# These tests require a real PostgreSQL instance (db-test service)
pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB_TESTS", "0") == "1",
    reason="SKIP_DB_TESTS=1 set; skipping integration tests requiring PostgreSQL",
)

EXPECTED_TABLES = [
    "owners",
    "agents",
    "problem_classes",
    "problems",
    "submissions",
    "calibration_results",
    "rating_history",
]

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://mergegate:mergegate@localhost:5433/mergegate_test",
)


def _run_alembic(command: list[str]) -> subprocess.CompletedProcess:
    """Run an alembic CLI command against the test database."""
    env = os.environ.copy()
    # Ensure the asyncpg driver is specified in the URL
    db_url = TEST_DB_URL
    if db_url.startswith("postgresql://") and "+asyncpg" not in db_url:
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    env["DATABASE_URL"] = db_url
    return subprocess.run(
        ["alembic"] + command,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _query_db(sql: str) -> str:
    """Run a psql query against the test database and return output."""
    result = subprocess.run(
        [
            "psql",
            TEST_DB_URL,
            "-t",
            "-A",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def test_alembic_upgrade_head():
    """alembic upgrade head runs without errors on a fresh database (INFR-04)."""
    # Downgrade first to ensure clean state
    _run_alembic(["downgrade", "base"])

    result = _run_alembic(["upgrade", "head"])
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_all_expected_tables_exist():
    """After upgrade head, all 7 expected tables exist (INFR-04)."""
    # Ensure we're at head
    _run_alembic(["upgrade", "head"])

    tables_output = _query_db(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename != 'alembic_version' ORDER BY tablename;"
    )
    tables = [t.strip() for t in tables_output.split("\n") if t.strip()]

    for expected in EXPECTED_TABLES:
        assert expected in tables, f"Table '{expected}' not found. Found: {tables}"


def test_leaderboard_materialized_view_exists():
    """The leaderboard materialized view exists after migration (INFR-04)."""
    _run_alembic(["upgrade", "head"])

    views = _query_db("SELECT matviewname FROM pg_matviews WHERE schemaname = 'public';")
    assert "leaderboard" in views, f"Materialized view 'leaderboard' not found. Found: {views}"


def test_timestamp_columns_are_timestamptz():
    """All timestamp columns use TIMESTAMPTZ type (INFR-06)."""
    _run_alembic(["upgrade", "head"])

    # Query information_schema for timestamp columns not using TIMESTAMPTZ
    result = _query_db(
        "SELECT table_name, column_name, data_type "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "AND (column_name LIKE '%_at' OR column_name LIKE '%_time%') "
        "AND data_type != 'timestamp with time zone' "
        "ORDER BY table_name, column_name;"
    )
    assert result == "", f"Found timestamp columns not using TIMESTAMPTZ: {result}"
