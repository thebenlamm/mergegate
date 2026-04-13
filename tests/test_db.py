"""Tests for asyncpg pool connectivity (INFR-03).

Tests real database connectivity against the docker-compose db-test service.
Requires: docker-compose db-test service running on port 5433.

Skip with: SKIP_DB_TESTS=1 pytest tests/test_db.py
"""

import os

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_DB_TESTS", "0") == "1",
    reason="SKIP_DB_TESTS=1 set; skipping integration tests requiring PostgreSQL",
)

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://mergegate:mergegate@localhost:5433/mergegate_test",
)


@pytest.fixture
async def pool():
    """Create a real asyncpg pool against the test database."""
    p = await asyncpg.create_pool(dsn=TEST_DB_URL, min_size=1, max_size=2)
    yield p
    await p.close()


async def test_pool_connectivity(pool):
    """asyncpg pool can connect and execute a simple query (INFR-03)."""
    async with pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
    assert result == 1


async def test_pool_returns_server_version(pool):
    """asyncpg pool can query PostgreSQL server version (INFR-03)."""
    async with pool.acquire() as conn:
        version = await conn.fetchval("SHOW server_version")
    assert version is not None
    # Verify it's PostgreSQL 16.x
    assert version.startswith("16"), f"Expected PG 16.x, got {version}"


async def test_pool_acquire_release(pool):
    """Connections are properly returned to pool after use (INFR-03)."""
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    # Pool size should remain within bounds after release
    assert pool.get_size() <= pool.get_max_size()
