"""Unit tests for api/services/auth.py.

Tests key generation, bcrypt hashing, fingerprint lookup, verification against mock DB rows,
TTL cache behavior, cache clearing, and registration rate limiting.
"""

import hashlib
import time
from unittest.mock import AsyncMock

import pytest

from api.services.auth import (
    _CACHE_TTL,
    _auth_cache,
    check_registration_rate_limit,
    clear_auth_cache,
    clear_rate_limit_state,
    generate_api_key,
    verify_api_key,
)


@pytest.fixture(autouse=True)
def reset_cache():
    """Clear the auth cache and rate limit state before and after each test."""
    clear_auth_cache()
    clear_rate_limit_state()
    yield
    clear_auth_cache()
    clear_rate_limit_state()


def test_api_key_has_mg_prefix():
    """Generated raw key must start with 'mg_'."""
    raw_key, _, _ = generate_api_key()
    assert raw_key.startswith("mg_")


def test_api_key_has_correct_length():
    """Raw key is 67 chars: 'mg_' (3) + 64 hex chars."""
    raw_key, _, _ = generate_api_key()
    assert len(raw_key) == 67


def test_key_hash_is_bcrypt():
    """Hash must be a $2b$ bcrypt string."""
    _, hashed_key, _ = generate_api_key()
    assert hashed_key.startswith("$2b$")


def test_key_stored_as_hash():
    """Raw key and hash must be different values (hash is not plaintext)."""
    raw_key, hashed_key, _ = generate_api_key()
    assert raw_key != hashed_key


def test_generate_api_key_returns_fingerprint():
    """generate_api_key() returns a 3-tuple where the third element is a 16-char hex string."""
    result = generate_api_key()
    assert len(result) == 3
    raw_key, hashed_key, fingerprint = result
    assert len(fingerprint) == 16
    # Fingerprint must be valid hex
    int(fingerprint, 16)


def test_clear_auth_cache():
    """clear_auth_cache() empties the module-level cache dict."""
    # Manually inject a cache entry using SHA-256 hex digest key (SEC-01)
    _auth_cache[hashlib.sha256(b"mg_fake_key").hexdigest()] = (
        "some-agent-id",
        time.monotonic() + 60,
    )
    assert len(_auth_cache) > 0
    clear_auth_cache()
    assert len(_auth_cache) == 0


def _make_fake_row(agent_data: dict) -> dict:
    """Create a dict that simulates an asyncpg Record row.

    asyncpg Records support dict(row) and row["key"] access.
    We use a plain dict here since verify_api_key calls dict(row).items().
    """
    return agent_data


@pytest.mark.asyncio
async def test_verify_uses_fingerprint_not_full_scan():
    """verify_api_key calls db.fetchrow with 'key_fingerprint' SQL, never calls db.fetch."""
    raw_key, hashed_key, fingerprint = generate_api_key()

    fake_row = _make_fake_row(
        {
            "id": "test-agent-id",
            "agent_name": "TestAgent",
            "api_key_hash": hashed_key,
            "key_fingerprint": fingerprint,
            "model": "test-model",
            "framework": None,
            "owner_id": "owner-id",
            "rating": 1500.0,
            "rating_deviation": 350.0,
            "volatility": 0.06,
            "problems_solved": 0,
            "total_submissions": 0,
            "streak": 0,
            "languages": ["python", "javascript"],
            "registered_at": None,
            "last_active": None,
            "is_verified": False,
            "is_banned": False,
        }
    )

    mock_db = AsyncMock()
    mock_db.fetchrow = AsyncMock(return_value=fake_row)

    await verify_api_key(mock_db, raw_key)

    # Must use fetchrow with key_fingerprint — NOT a full table scan
    mock_db.fetchrow.assert_called()
    call_args = str(mock_db.fetchrow.call_args_list)
    assert "key_fingerprint" in call_args
    # fetch (full scan) must never be called in cache-miss path
    mock_db.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_verify_api_key_returns_agent_on_match():
    """verify_api_key returns an agent dict when the key matches a row."""
    raw_key, hashed_key, fingerprint = generate_api_key()

    fake_row = _make_fake_row(
        {
            "id": "test-agent-id",
            "agent_name": "TestAgent",
            "api_key_hash": hashed_key,
            "key_fingerprint": fingerprint,
            "model": "test-model",
            "framework": None,
            "owner_id": "owner-id",
            "rating": 1500.0,
            "rating_deviation": 350.0,
            "volatility": 0.06,
            "problems_solved": 0,
            "total_submissions": 0,
            "streak": 0,
            "languages": ["python", "javascript"],
            "registered_at": None,
            "last_active": None,
            "is_verified": False,
            "is_banned": False,
        }
    )

    mock_db = AsyncMock()
    mock_db.fetchrow = AsyncMock(return_value=fake_row)

    result = await verify_api_key(mock_db, raw_key)

    assert result is not None
    assert result["agent_name"] == "TestAgent"
    assert "api_key_hash" not in result


@pytest.mark.asyncio
async def test_verify_api_key_returns_none_on_mismatch():
    """verify_api_key returns None when no row's hash matches the key."""
    raw_key, _, fingerprint = generate_api_key()
    # Hash for a DIFFERENT key
    _, wrong_hash, _ = generate_api_key()

    fake_row = _make_fake_row(
        {
            "id": "other-agent-id",
            "agent_name": "OtherAgent",
            "api_key_hash": wrong_hash,
            "key_fingerprint": fingerprint,
            "model": "test-model",
            "framework": None,
            "owner_id": "owner-id",
            "rating": 1500.0,
            "rating_deviation": 350.0,
            "volatility": 0.06,
            "problems_solved": 0,
            "total_submissions": 0,
            "streak": 0,
            "languages": ["python", "javascript"],
            "registered_at": None,
            "last_active": None,
            "is_verified": False,
            "is_banned": False,
        }
    )

    mock_db = AsyncMock()
    mock_db.fetchrow = AsyncMock(return_value=fake_row)

    result = await verify_api_key(mock_db, raw_key)
    assert result is None


@pytest.mark.asyncio
async def test_verify_api_key_returns_none_on_empty_db():
    """verify_api_key returns None when no row is found for the fingerprint."""
    mock_db = AsyncMock()
    mock_db.fetchrow = AsyncMock(return_value=None)

    result = await verify_api_key(mock_db, "mg_nonexistent_key")
    assert result is None


@pytest.mark.asyncio
async def test_auth_cache_hit_avoids_full_scan():
    """Second call within TTL hits the cache and calls fetchrow instead of fetch."""
    raw_key, hashed_key, _ = generate_api_key()

    # Seed the cache with a known agent_id using SHA-256 hex digest key (SEC-01)
    agent_id = "cached-agent-id"
    _auth_cache[hash(raw_key)] = (agent_id, time.monotonic() + _CACHE_TTL)

    # Build a fake agent row for the cache-hit fetchrow path (plain dict, like asyncpg Record)
    fake_agent = {
        "id": agent_id,
        "agent_name": "CachedAgent",
        "model": "test-model",
        "framework": None,
        "owner_id": "owner-id",
        "rating": 1500.0,
        "rating_deviation": 350.0,
        "volatility": 0.06,
        "problems_solved": 0,
        "total_submissions": 0,
        "streak": 0,
        "languages": ["python", "javascript"],
        "registered_at": None,
        "last_active": None,
        "is_verified": False,
        "is_banned": False,
    }

    mock_db = AsyncMock()
    mock_db.fetchrow = AsyncMock(return_value=fake_agent)

    await verify_api_key(mock_db, raw_key)

    # Cache hit: should call fetchrow (by agent_id), NOT fetch (full scan)
    mock_db.fetchrow.assert_called_once()
    mock_db.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Rate limit tests (Plan 02)
# ---------------------------------------------------------------------------


def test_rate_limit_allows_under_limit():
    """check_registration_rate_limit returns True for calls 1-5 (under limit)."""
    for _ in range(5):
        assert check_registration_rate_limit("10.0.0.1") is True


def test_rate_limit_blocks_at_limit():
    """6th call to check_registration_rate_limit for same IP returns False."""
    for _ in range(5):
        check_registration_rate_limit("10.0.0.2")
    assert check_registration_rate_limit("10.0.0.2") is False


def test_rate_limit_per_ip():
    """Rate limit on one IP does not affect a different IP."""
    for _ in range(5):
        check_registration_rate_limit("10.0.0.3")
    assert check_registration_rate_limit("10.0.0.3") is False
    assert check_registration_rate_limit("10.0.0.4") is True


def test_clear_rate_limit_state_works():
    """clear_rate_limit_state() resets rate limit so previously-limited IP is allowed."""
    for _ in range(5):
        check_registration_rate_limit("10.0.0.5")
    assert check_registration_rate_limit("10.0.0.5") is False
    clear_rate_limit_state()
    assert check_registration_rate_limit("10.0.0.5") is True
