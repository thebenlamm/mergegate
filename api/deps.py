"""FastAPI dependency injection for MergeGate API.

Provides database connection dependencies and authentication for route handlers.
"""

from collections.abc import AsyncGenerator

import asyncpg
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.errors import AppError
from api.services import auth as auth_service


async def get_db(request: Request) -> AsyncGenerator[asyncpg.Connection, None]:
    """Yield a database connection from the app's asyncpg pool.

    Acquires a connection for the duration of the request and releases
    it back to the pool when the request completes (even on errors).

    Usage in routes:
        async def my_route(db: asyncpg.Connection = Depends(get_db)):
            ...
    """
    async with request.app.state.pool.acquire(timeout=30) as connection:
        yield connection


# HTTPBearer with auto_error=False so we return 401 (not 403) on missing header
security = HTTPBearer(auto_error=False)


async def get_current_agent(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    """Authenticate the request and return the agent row dict.

    Raises AppError(401, AUTH_INVALID) if:
    - Authorization header is missing
    - Token does not match any non-banned agent in the database

    Usage in routes:
        async def my_route(agent: dict = Depends(get_current_agent)):
            agent_id = agent["id"]
            ...
    """
    if credentials is None:
        raise AppError(
            "Authorization header required. Use: Authorization: Bearer mg_...",
            "AUTH_INVALID",
            status=401,
        )
    agent = await auth_service.verify_api_key(db, credentials.credentials)
    if agent is None:
        raise AppError(
            "Invalid or expired API key.",
            "AUTH_INVALID",
            status=401,
        )
    return agent
