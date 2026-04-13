"""Global error handling for the MergeGate API.

All errors are returned as {"error": "message", "code": "ERROR_CODE"} JSON.
Never returns stack traces or HTML to clients.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException, RequestValidationError
import structlog

logger = structlog.get_logger()


class AppError(Exception):
    """Application-level error with structured error code.

    Raise this anywhere in route handlers for expected error conditions.
    The global exception handler will convert it to {"error": ..., "code": ...} JSON.
    """

    def __init__(self, message: str, code: str, status: int = 400):
        self.message = message
        self.code = code
        self.status = status
        super().__init__(message)


def install_error_handlers(app) -> None:
    """Register global exception handlers on the FastAPI app.

    Must be called after app creation and before first request.
    Handles: AppError, RequestValidationError, and all unhandled exceptions.
    """

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Convert FastAPI HTTPException to {error, code} JSON shape.

        If exc.detail is already a dict with a 'code' key (e.g. from a raise
        HTTPException(status_code=..., detail={"error": ..., "code": ...})),
        return it directly. Otherwise, wrap the detail string in our standard shape.
        """
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": str(exc.detail), "code": f"HTTP_{exc.status_code}"},
        )

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={"error": exc.message, "code": exc.code},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Override FastAPI's default 422 handler — sanitized, no input echo."""
        errors = exc.errors()
        sanitized = [
            {"field": " -> ".join(str(part) for part in e["loc"]), "issue": e["type"]}
            for e in errors
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": f"Validation failed on {len(errors)} field(s)",
                "code": "VALIDATION_ERROR",
                "details": sanitized,
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """Catch-all handler — logs full traceback, returns safe message to client.

        This handler runs inside ServerErrorMiddleware (outermost layer), which means
        RequestIDMiddleware may have already set request_id in scope. We read it back
        and inject it into the response headers so error responses also carry the ID.
        """
        logger.error(
            "unhandled_error",
            exc_info=exc,
            error_type=type(exc).__name__,
        )
        # Retrieve the request_id set by RequestIDMiddleware (if present)
        request_id = getattr(request.state, "request_id", None)
        headers = {}
        if request_id:
            headers["X-Request-ID"] = request_id
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "code": "INTERNAL_ERROR"},
            headers=headers,
        )
