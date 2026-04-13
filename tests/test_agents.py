"""Integration tests for agent registration and profile routes.

Tests POST /api/v1/agents/register (success, duplicate name, validation).
Tests auth-protected endpoints return 401 with correct error shape.
Tests GET /api/v1/agents/me (profile fields, tier, rating display, streak, history).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from httpx import AsyncClient, ASGITransport

from api.main import app
from api.services.auth import clear_rate_limit_state
from api.utils import compute_tier, format_rating_display


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear rate limit state before and after each test."""
    clear_rate_limit_state()
    yield
    clear_rate_limit_state()


@pytest.fixture
async def client_no_auth():
    """Test client with mocked DB pool, no auth override."""
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=1)
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_cm)
    app.state.pool = mock_pool

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac, mock_conn


@pytest.mark.asyncio
async def test_register_success(client_no_auth):
    """POST /api/v1/agents/register returns 201 with one-time mg_ API key."""
    ac, mock_conn = client_no_auth

    # Mock owner upsert (INSERT INTO owners), owner SELECT, agent INSERT, rating_history INSERT
    owner_id = "owner-uuid-1234"
    mock_owner_row = MagicMock()
    mock_owner_row.__getitem__ = MagicMock(return_value=owner_id)

    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=mock_owner_row)

    response = await ac.post(
        "/api/v1/agents/register",
        json={
            "agent_name": "MyAgent",
            "model": "claude-opus-4",
            "framework": "openclaw",
            "owner_handle": "@testuser",
            "languages": ["python", "javascript"],
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert "agent_id" in data
    assert data["api_key"].startswith("mg_")
    assert len(data["api_key"]) == 67
    assert data["rating"] == 1500.0
    assert data["rating_deviation"] == 350.0
    assert "Store your api_key" in data["message"]
    assert data["agent_name"] == "MyAgent"


@pytest.mark.asyncio
async def test_register_duplicate_name(client_no_auth):
    """POST /api/v1/agents/register returns 409 AGENT_NAME_TAKEN on duplicate."""
    ac, mock_conn = client_no_auth

    owner_id = "owner-uuid-5678"
    mock_owner_row = MagicMock()
    mock_owner_row.__getitem__ = MagicMock(return_value=owner_id)

    # First execute (owners INSERT) succeeds; second execute (agents INSERT) raises UniqueViolationError
    execute_call_count = 0

    async def mock_execute(sql, *args):
        nonlocal execute_call_count
        execute_call_count += 1
        if execute_call_count == 2:
            raise asyncpg.UniqueViolationError("duplicate key value violates unique constraint")
        return None

    mock_conn.execute = mock_execute
    mock_conn.fetchrow = AsyncMock(return_value=mock_owner_row)

    response = await ac.post(
        "/api/v1/agents/register",
        json={
            "agent_name": "ExistingAgent",
            "model": "gpt-4o",
            "owner_handle": "@user",
        },
    )

    assert response.status_code == 409
    data = response.json()
    assert data["code"] == "AGENT_NAME_TAKEN"


@pytest.mark.asyncio
async def test_me_without_auth_returns_401(client):
    """GET /api/v1/agents/me with no Authorization header returns 401 AUTH_INVALID."""
    response = await client.get("/api/v1/agents/me")
    assert response.status_code == 401
    data = response.json()
    assert data["code"] == "AUTH_INVALID"


@pytest.mark.asyncio
async def test_invalid_token_returns_401(client_no_auth):
    """GET /api/v1/agents/me with an invalid Bearer token returns 401 AUTH_INVALID."""
    ac, mock_conn = client_no_auth

    # verify_api_key returns None for invalid keys
    with patch("api.deps.auth_service.verify_api_key", return_value=None) as mock_verify:
        # Make mock_verify awaitable
        mock_verify.return_value = None

        async def async_none(*args, **kwargs):
            return None

        with patch("api.deps.auth_service.verify_api_key", side_effect=async_none):
            response = await ac.get(
                "/api/v1/agents/me",
                headers={"Authorization": "Bearer mg_invalid_key_that_does_not_exist"},
            )

    assert response.status_code == 401
    data = response.json()
    assert data["code"] == "AUTH_INVALID"


@pytest.mark.asyncio
async def test_owner_deduplication(client_no_auth):
    """Two registrations with the same owner_handle reuse the same owner row."""
    ac, mock_conn = client_no_auth

    owner_id = "shared-owner-uuid"
    mock_owner_row = MagicMock()
    mock_owner_row.__getitem__ = MagicMock(return_value=owner_id)

    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=mock_owner_row)

    # Register first agent
    resp1 = await ac.post(
        "/api/v1/agents/register",
        json={
            "agent_name": "AgentAlpha",
            "model": "claude-opus-4",
            "owner_handle": "@sharedowner",
        },
    )
    # Register second agent with same owner_handle
    resp2 = await ac.post(
        "/api/v1/agents/register",
        json={
            "agent_name": "AgentBeta",
            "model": "claude-opus-4",
            "owner_handle": "@sharedowner",
        },
    )

    assert resp1.status_code == 201
    assert resp2.status_code == 201

    # Verify ON CONFLICT upsert was called twice (once per registration)
    upsert_calls = [call for call in mock_conn.execute.call_args_list if "ON CONFLICT" in str(call)]
    assert len(upsert_calls) == 2

    # Both registrations should have fetched the same owner row
    assert mock_conn.fetchrow.call_count == 2


@pytest.mark.asyncio
async def test_register_missing_required_fields(client_no_auth):
    """POST /api/v1/agents/register returns 422 if required fields are missing."""
    ac, _ = client_no_auth

    response = await ac.post(
        "/api/v1/agents/register",
        json={"model": "gpt-4o"},  # missing agent_name and owner_handle
    )

    assert response.status_code == 422
    data = response.json()
    assert data["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Profile endpoint tests (Plan 03)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_fields(client_authed):
    """GET /api/v1/agents/me returns all expected profile fields with correct values."""
    ac = client_authed

    # Build mock DB return values in call order:
    # 1. fetchrow for leaderboard (global_rank)
    # 2. fetchrow for owners (handle)
    # 3. fetch for category_breakdown
    # 4. fetch for rating_history
    mock_conn = MagicMock()

    leaderboard_row = MagicMock()
    leaderboard_row.__getitem__ = MagicMock(side_effect=lambda k: 1 if k == "global_rank" else None)

    owner_row = MagicMock()
    owner_row.__getitem__ = MagicMock(side_effect=lambda k: "@testowner" if k == "handle" else None)

    cat_row = MagicMock()
    cat_row.__getitem__ = MagicMock(
        side_effect=lambda k: {"cat": "strings", "attempted": 5, "solved": 3}[k]
    )

    hist_row = MagicMock()
    recorded_dt = datetime(2026, 3, 28, tzinfo=timezone.utc)
    hist_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "recorded_at": recorded_dt,
            "rating": 1500.0,
            "rating_deviation": 350.0,
        }[k]
    )
    hist_row.get = MagicMock(
        side_effect=lambda k, default=None: {
            "recorded_at": recorded_dt,
            "rating": 1500.0,
            "rating_deviation": 350.0,
        }.get(k, default)
    )

    fetchrow_calls = [leaderboard_row, owner_row]
    fetch_calls = [[cat_row], [hist_row]]

    mock_conn.fetchrow = AsyncMock(side_effect=fetchrow_calls)
    mock_conn.fetch = AsyncMock(side_effect=fetch_calls)

    # Override the pool so get_db yields our mock_conn
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_cm)
    app.state.pool = mock_pool

    response = await ac.get("/api/v1/agents/me")

    assert response.status_code == 200
    data = response.json()

    assert data["rating"] == 1500.0
    assert data["rating_deviation"] == 350.0
    assert data["rating_display"] == "1500 \u00b1 350"
    assert data["tier"] == "platinum"
    assert data["global_rank"] == 1
    assert data["problems_solved"] == 0
    assert data["acceptance_rate"] == 0.0
    # streak removed from ProfileResponse (API-03) — always was 0, never implemented
    assert "streak" not in data
    assert "strings" in data["category_breakdown"]
    assert data["category_breakdown"]["strings"]["solved"] == 3
    assert data["category_breakdown"]["strings"]["attempted"] == 5
    assert isinstance(data["rating_history"], list)
    assert len(data["rating_history"]) >= 1


@pytest.mark.asyncio
async def test_register_stores_fingerprint(client_no_auth):
    """POST /api/v1/agents/register INSERT SQL includes key_fingerprint column."""
    ac, mock_conn = client_no_auth

    owner_id = "owner-uuid-fp"
    mock_owner_row = MagicMock()
    mock_owner_row.__getitem__ = MagicMock(return_value=owner_id)

    executed_sqls: list[str] = []

    async def capture_execute(sql, *args):
        executed_sqls.append(sql)
        return None

    mock_conn.execute = capture_execute
    mock_conn.fetchrow = AsyncMock(return_value=mock_owner_row)

    response = await ac.post(
        "/api/v1/agents/register",
        json={
            "agent_name": "FingerprintAgent",
            "model": "claude-opus-4",
            "owner_handle": "@fpowner",
        },
    )

    assert response.status_code == 201
    agent_inserts = [sql for sql in executed_sqls if "INSERT INTO agents" in sql]
    assert len(agent_inserts) >= 1, "Expected at least one INSERT INTO agents"
    assert "key_fingerprint" in agent_inserts[0], (
        "INSERT INTO agents SQL must include key_fingerprint column"
    )


@pytest.mark.asyncio
async def test_rating_history_on_register(client_no_auth):
    """Registration route inserts an initial row into rating_history."""
    ac, mock_conn = client_no_auth

    owner_id = "owner-uuid-hist"
    mock_owner_row = MagicMock()
    mock_owner_row.__getitem__ = MagicMock(return_value=owner_id)

    executed_sqls: list[str] = []

    async def capture_execute(sql, *args):
        executed_sqls.append(sql)
        return None

    mock_conn.execute = capture_execute
    mock_conn.fetchrow = AsyncMock(return_value=mock_owner_row)

    response = await ac.post(
        "/api/v1/agents/register",
        json={
            "agent_name": "HistoryAgent",
            "model": "claude-opus-4",
            "owner_handle": "@histowner",
        },
    )

    assert response.status_code == 201
    history_inserts = [sql for sql in executed_sqls if "rating_history" in sql]
    assert len(history_inserts) >= 1, "Expected at least one INSERT INTO rating_history"


@pytest.mark.asyncio
async def test_streak_removed_from_profile(client_authed):
    """GET /api/v1/agents/me does not include streak field (API-03).

    streak was always 0 (never implemented) and has been removed from
    ProfileResponse to avoid exposing a dead field.
    """
    ac = client_authed

    # Minimal mock: no leaderboard rank, no owner, no categories, no history
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(side_effect=[[], []])

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_cm)
    app.state.pool = mock_pool

    response = await ac.get("/api/v1/agents/me")
    assert response.status_code == 200
    data = response.json()
    assert "streak" not in data


# ---------------------------------------------------------------------------
# Tier / display unit tests (no HTTP, pure functions)
# ---------------------------------------------------------------------------


def test_tier_computation_boundaries():
    """compute_tier returns correct tier label at every boundary value."""
    assert compute_tier(999) == "bronze"
    assert compute_tier(1000) == "silver"
    assert compute_tier(1199) == "silver"
    assert compute_tier(1200) == "gold"
    assert compute_tier(1399) == "gold"
    assert compute_tier(1400) == "platinum"
    assert compute_tier(1599) == "platinum"
    assert compute_tier(1600) == "diamond"
    assert compute_tier(1799) == "diamond"
    assert compute_tier(1800) == "grandmaster"
    assert compute_tier(2500) == "grandmaster"


def test_rating_display_format():
    """format_rating_display returns correct '±' string with rounded values."""
    assert format_rating_display(1500.0, 350.0) == "1500 \u00b1 350"
    assert format_rating_display(1847.3, 43.7) == "1847 \u00b1 44"


@pytest.mark.asyncio
async def test_null_byte_in_agent_name_returns_422(client_no_auth):
    """SEC-08: Null bytes in string fields must be rejected with 422."""
    ac, mock_conn = client_no_auth
    resp = await ac.post(
        "/api/v1/agents/register",
        json={
            "agent_name": "test\x00bad",
            "model": "test-model",
            "owner_handle": "test_owner",
        },
    )
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == "VALIDATION_ERROR"
    # No echoed user input in the response
    response_text = str(data)
    assert "test\x00bad" not in response_text


# ---------------------------------------------------------------------------
# Rate limiting tests (Plan 02 — SEC-04)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_rate_limit(client_no_auth):
    """6th registration from same IP in 1 hour returns 429 RATE_LIMIT_EXCEEDED."""
    ac, mock_conn = client_no_auth
    owner_id = "owner-uuid-rate"
    mock_owner_row = MagicMock()
    mock_owner_row.__getitem__ = MagicMock(return_value=owner_id)
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=mock_owner_row)

    for i in range(5):
        resp = await ac.post(
            "/api/v1/agents/register",
            json={
                "agent_name": f"RateLimitAgent{i}",
                "model": "test-model",
                "owner_handle": "@rateowner",
                "languages": ["python"],
            },
        )
        assert resp.status_code == 201

    resp = await ac.post(
        "/api/v1/agents/register",
        json={
            "agent_name": "RateLimitAgent6",
            "model": "test-model",
            "owner_handle": "@rateowner",
            "languages": ["python"],
        },
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_register_at_rate_limit_boundary(client_no_auth):
    """5th registration from same IP succeeds (boundary case)."""
    ac, mock_conn = client_no_auth
    owner_id = "owner-uuid-boundary"
    mock_owner_row = MagicMock()
    mock_owner_row.__getitem__ = MagicMock(return_value=owner_id)
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=mock_owner_row)

    for i in range(4):
        await ac.post(
            "/api/v1/agents/register",
            json={
                "agent_name": f"BoundaryAgent{i}",
                "model": "test-model",
                "owner_handle": "@boundaryowner",
            },
        )

    resp = await ac.post(
        "/api/v1/agents/register",
        json={
            "agent_name": "BoundaryAgent5",
            "model": "test-model",
            "owner_handle": "@boundaryowner",
        },
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# API-03: ProfileResponse must not include streak field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_no_streak(client_authed):
    """API-03: GET /api/v1/agents/me response JSON must NOT contain 'streak' key.

    streak is always 0 (never implemented) and was removed from ProfileResponse
    to avoid exposing a dead field in the public API contract.
    """
    ac = client_authed

    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock(return_value=None)  # no leaderboard row, no owner
    mock_conn.fetch = AsyncMock(side_effect=[[], []])  # no categories, no history

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_cm)
    app.state.pool = mock_pool

    response = await ac.get("/api/v1/agents/me")
    assert response.status_code == 200
    data = response.json()

    assert "streak" not in data, (
        "API-03: 'streak' field must be removed from ProfileResponse — it was always 0"
    )
