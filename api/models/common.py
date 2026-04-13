"""Shared Pydantic response models used across all routes.

ErrorResponse: Standard error shape returned by all error handlers.
PaginatedResponse: Generic wrapper for paginated list endpoints.
"""

from typing import Generic, List, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorResponse(BaseModel):
    """Standard error response shape.

    All error handlers return this shape:
        {"error": "Human-readable message", "code": "MACHINE_CODE"}
    """

    error: str
    code: str


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response for list endpoints.

    All paginated endpoints return this shape with limit + offset query params.
    The total field allows clients to determine if more pages exist.
    """

    items: List[T]
    total: int
    limit: int
    offset: int
