"""Structured logging configuration for MergeGate API.

Configures structlog with JSON output (or console for dev) and a
RequestIDMiddleware that binds a unique request_id to every log event
within a request's scope.
"""

import logging
import uuid

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

from api.config import get_settings


def configure_logging() -> None:
    """Configure structlog for structured JSON (or console) output.

    Must be called once at app startup (in lifespan) before any log calls.
    Reads log_format and log_level from settings.
    """
    settings = get_settings()
    if settings.log_format == "console":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


class RequestIDMiddleware:
    """Pure ASGI middleware that generates a unique request_id per request.

    Implemented as a raw ASGI middleware (not BaseHTTPMiddleware) so it
    doesn't interfere with FastAPI's exception handler chain. Binds
    request_id to structlog contextvars so all log events within the
    request automatically include it. Sets X-Request-ID response header.

    Stores request_id in scope["state"]["request_id"] so error handlers
    (run by ServerErrorMiddleware, which wraps this middleware) can also
    include the header by reading from scope.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())

        # Store in scope so exception handlers (ServerErrorMiddleware) can access it
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        log = structlog.get_logger()
        log.info(
            "request_started",
            method=scope.get("method", ""),
            path=scope.get("path", ""),
        )

        async def send_with_request_id(message) -> None:
            if message["type"] == "http.response.start":
                # Inject X-Request-ID into response headers
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
                log.info("request_completed", status_code=message.get("status", 0))
            await send(message)

        await self.app(scope, receive, send_with_request_id)
