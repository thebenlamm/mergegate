"""API key generation and bcrypt verification with TTL cache and registration rate limiting.

Provides:
- generate_api_key(): creates an mg_-prefixed raw key, bcrypt hash, and SHA-256 fingerprint
- verify_api_key(): verifies raw key via O(1) fingerprint index + single bcrypt check
- clear_auth_cache(): clears the TTL cache (for testing)
- check_registration_rate_limit(ip): sliding window rate limit — max 5 per IP per hour
- clear_rate_limit_state(): clears in-memory rate limit state (for testing)

Security notes:
- Raw keys are NEVER stored in the database; only bcrypt hashes are stored
- Fingerprint is first 16 hex chars of SHA-256(raw_key) — collision-resistant, indexed
- Auth lookup uses WHERE key_fingerprint = $1 (O(1) index scan), NOT a full table scan
- bcrypt runs in a thread pool executor to avoid blocking the async event loop
- TTL cache avoids per-request bcrypt checks (bcrypt is intentionally slow)
- Rate limiter uses in-memory sliding window; safe for single-process deployments
"""

import asyncio
import hashlib
import secrets
import time
from collections import deque
from functools import partial
from typing import Dict

import asyncpg
import bcrypt

# In-process TTL auth cache: {hash(raw_key): (agent_id_str, expiry_monotonic_ts)}
_auth_cache: dict[int, tuple[str, float]] = {}
_CACHE_TTL = 60.0  # seconds

# In-memory sliding window rate limiter for registration: {ip: deque of timestamps}
_reg_attempts: Dict[str, deque] = {}
_REG_WINDOW = 3600.0  # 1 hour in seconds
_REG_LIMIT = 5


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new mg_-prefixed API key, its bcrypt hash, and a SHA-256 fingerprint.

    Returns:
        (raw_key, hashed_key, fingerprint) — raw_key is 67 chars ("mg_" + 64 hex),
        hashed_key is a $2b$ bcrypt string, fingerprint is the first 16 hex chars of
        SHA-256(raw_key). Store ONLY the hash and fingerprint in DB.
        The raw key must be returned to the agent once and never stored.
    """
    raw_key = "mg_" + secrets.token_hex(32)  # 3 + 64 = 67 chars
    hashed_key = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12)).decode("utf-8")
    fingerprint = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
    return raw_key, hashed_key, fingerprint


async def verify_api_key(db: asyncpg.Connection, raw_key: str) -> dict | None:
    """Verify a raw API key against the database and return the agent dict.

    Uses an in-process TTL cache so bcrypt is only run once per TTL period.
    Cache miss path: O(1) fingerprint index lookup, then single bcrypt check.
    bcrypt verification runs in a thread pool executor to avoid blocking the
    async event loop (bcrypt is CPU-bound and intentionally slow).

    Args:
        db: asyncpg database connection
        raw_key: the raw mg_-prefixed key from the Authorization header

    Returns:
        Agent row dict (without api_key_hash) if key is valid, None otherwise.
    """
    loop = asyncio.get_running_loop()
    cache_key = hash(raw_key)
    now = time.monotonic()

    # Cache HIT path: key matches a known agent_id that hasn't expired
    if cache_key in _auth_cache:
        agent_id_str, expiry = _auth_cache[cache_key]
        if now < expiry:
            row = await db.fetchrow(
                """SELECT id, agent_name, model, framework, owner_id,
                          rating, rating_deviation, volatility,
                          problems_solved, total_submissions, streak, languages,
                          registered_at, last_active, is_verified, is_banned
                   FROM agents
                   WHERE id = $1 AND is_banned = FALSE""",
                agent_id_str,
            )
            if row is not None:
                return dict(row)
            # Agent was banned or deleted — evict from cache
            del _auth_cache[cache_key]

    # Cache MISS path: O(1) fingerprint lookup, then single bcrypt check
    fingerprint = hashlib.sha256(raw_key.encode()).hexdigest()[:16]

    row = await db.fetchrow(
        """SELECT id, agent_name, api_key_hash, model, framework, owner_id,
                  rating, rating_deviation, volatility,
                  problems_solved, total_submissions, streak, languages,
                  registered_at, last_active, is_verified, is_banned
           FROM agents
           WHERE key_fingerprint = $1 AND is_banned = FALSE""",
        fingerprint,
    )

    if row is None:
        return None

    # Single bcrypt check for the matched row
    matches = await loop.run_in_executor(
        None, partial(bcrypt.checkpw, raw_key.encode(), row["api_key_hash"].encode())
    )
    if not matches:
        return None

    agent_id_str = str(row["id"])
    # Store in cache with TTL
    _auth_cache[cache_key] = (agent_id_str, now + _CACHE_TTL)
    # Return all fields except api_key_hash
    result = {k: v for k, v in dict(row).items() if k != "api_key_hash"}
    return result


def clear_auth_cache() -> None:
    """Clear the in-process auth cache.

    Exposed primarily for testing so tests start with a clean cache.
    """
    _auth_cache.clear()


def check_registration_rate_limit(ip: str) -> bool:
    """Check if the given IP is allowed to register. Returns True if allowed.

    Uses an in-memory sliding window: tracks timestamps of recent registrations per IP.
    Expires entries older than _REG_WINDOW (1 hour). Safe for single-process async use (GIL).

    Returns:
        True if the IP has fewer than _REG_LIMIT (5) registrations in the last hour.
        False if the limit is reached or exceeded.
    """
    now = time.monotonic()
    if ip not in _reg_attempts:
        _reg_attempts[ip] = deque()
    attempts = _reg_attempts[ip]
    # Expire old entries outside the sliding window
    while attempts and (now - attempts[0]) > _REG_WINDOW:
        attempts.popleft()
    if len(attempts) >= _REG_LIMIT:
        return False
    attempts.append(now)
    return True


def clear_rate_limit_state() -> None:
    """Clear the in-memory registration rate limit state.

    Exposed for testing so tests start with a clean rate limit state.
    """
    _reg_attempts.clear()
