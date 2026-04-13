"""Health check endpoint for MergeGate API.

GET /health returns status, timestamp, version, and database connectivity.
Health endpoint acquires from pool directly (not via Depends(get_db)) so it
can catch pool failures gracefully and return "degraded" instead of 500.
"""

from datetime import datetime, timezone
from api.config import get_settings

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    """Return health status including database connectivity.

    Returns:
        status: "ok" if DB connected, "degraded" if DB unreachable
        timestamp: Current UTC time in ISO 8601 format
        version: API version string
        database: "connected" or "disconnected"
    """
    db_ok = False
    try:
        async with request.app.state.pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": get_settings().version,
        "database": "connected" if db_ok else "disconnected",
    }
