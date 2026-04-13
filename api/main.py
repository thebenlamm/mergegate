"""FastAPI application entry point for MergeGate API.

Creates the app with:
- asyncpg connection pool via lifespan (startup/shutdown)
- structlog structured logging with request ID middleware
- Global error handlers (AppError, validation, catch-all)
- Health endpoint
"""

import json
from contextlib import asynccontextmanager

import asyncpg
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import get_settings
from api.errors import install_error_handlers
from api.logging_config import RequestIDMiddleware, configure_logging
from api.routes.agents import router as agents_router
from api.routes.health import router as health_router
from api.routes.mergegate import router as mergegate_router

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: pool creation at startup, teardown at shutdown."""
    settings = get_settings()
    configure_logging()

    async def _init_connection(conn):
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )
        await conn.set_type_codec(
            "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )

    # Determine SSL mode from DSN query params; default to disabled for local dev
    dsn = settings.database_url
    import ssl as _ssl_mod

    ssl_ctx: bool | _ssl_mod.SSLContext = False
    if "sslmode=require" in dsn or "sslmode=verify" in dsn:
        ssl_ctx = True

    app.state.pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=20,
        max_queries=50000,
        max_inactive_connection_lifetime=300.0,
        command_timeout=30,
        init=_init_connection,
        ssl=ssl_ctx,
    )

    yield

    await app.state.pool.close()


app = FastAPI(
    title="MergeGate",
    description="Delegation benchmark for AI coding agents",
    version=get_settings().version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIDMiddleware)
install_error_handlers(app)
app.include_router(health_router)
app.include_router(agents_router)
app.include_router(mergegate_router)
