"""pytest fixtures for MergeGate API tests.

Provides an async HTTP client with a mocked DB pool so unit tests
don't require a real PostgreSQL instance.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock

from api.deps import get_current_agent
from api.main import app


@pytest.fixture
async def client():
    """Test client with mocked DB pool (no real PostgreSQL needed).

    Creates a mock asyncpg pool that returns mock connections so
    unit tests can exercise routes without a live database.
    """
    # Mock connection that responds to simple queries
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=1)

    # Mock pool context manager
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
        yield ac


@pytest.fixture
def mock_agent():
    """Agent dict matching the shape returned by get_current_agent.

    Use this in tests that need an authenticated agent context.
    """
    return {
        "id": "01234567-0123-7890-abcd-0123456789ab",
        "agent_name": "TestAgent",
        "model": "test-model",
        "framework": "test-framework",
        "owner_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "rating": 1500.0,
        "rating_deviation": 350.0,
        "volatility": 0.06,
        "problems_solved": 0,
        "total_submissions": 0,
        "streak": 0,
        "languages": ["python", "javascript"],
        "registered_at": "2026-03-28T00:00:00+00:00",
        "last_active": None,
        "is_verified": False,
        "is_banned": False,
    }


@pytest.fixture
async def client_authed(mock_agent):
    """Test client with auth dependency overridden to return mock_agent.

    The DB pool is mocked as in the `client` fixture.
    Use this for testing routes that require a valid Bearer token.
    """
    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=1)

    # Support async with db.transaction() (REL-05 advisory lock uses this)
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_cm)
    app.state.pool = mock_pool

    app.dependency_overrides[get_current_agent] = lambda: mock_agent

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.pop(get_current_agent, None)
