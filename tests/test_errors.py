"""Tests for global error handling (INFR-07, INFR-08).

INFR-07: All errors return {"error": ..., "code": ...} JSON — never stack traces or HTML.
INFR-08: PaginatedResponse model has items, total, limit, offset fields.

Registers temporary test routes on the app to trigger each error type.
"""

import pytest
from fastapi import APIRouter

from api.main import app
from api.errors import AppError

# Register temporary test routes that trigger each error type.
# These are registered at module import time so they persist across the test session.
_test_router = APIRouter()


@_test_router.get("/test/app-error")
async def trigger_app_error():
    raise AppError(message="Test error", code="TEST_ERROR", status=418)


@_test_router.get("/test/unhandled-error")
async def trigger_unhandled():
    raise RuntimeError("Unexpected failure")


@_test_router.get("/test/validation-error")
async def trigger_validation(num: int):
    """Requesting with ?num=notanumber triggers RequestValidationError."""
    return {"num": num}


app.include_router(_test_router)


# INFR-07: AppError returns structured {error, code} response
async def test_app_error_returns_structured_json(client):
    """AppError raises map to {error, code} with correct status (INFR-07)."""
    response = await client.get("/test/app-error")
    assert response.status_code == 418
    data = response.json()
    assert data == {"error": "Test error", "code": "TEST_ERROR"}


# INFR-07: Unhandled exceptions return structured {error, code} — not stack traces
async def test_unhandled_error_returns_structured_json(client):
    """Unhandled exceptions return {error, code} without stack traces (INFR-07)."""
    response = await client.get("/test/unhandled-error")
    assert response.status_code == 500
    data = response.json()
    assert "error" in data
    assert "code" in data
    assert data["code"] == "INTERNAL_ERROR"
    # Must NOT contain a traceback or HTML
    assert "Traceback" not in str(data)
    assert "<html" not in str(data).lower()


# INFR-07: Validation errors return structured {error, code} — not FastAPI default
async def test_validation_error_returns_structured_json(client):
    """Pydantic validation errors return {error, code}, not FastAPI's default 422 shape (INFR-07)."""
    response = await client.get("/test/validation-error?num=notanumber")
    assert response.status_code == 422
    data = response.json()
    assert "error" in data
    assert "code" in data
    assert data["code"] == "VALIDATION_ERROR"
    # Must NOT have FastAPI's default "detail" array shape
    assert "detail" not in data


# INFR-02: Error responses also include request ID header
async def test_error_response_has_request_id(client):
    """Error responses include X-Request-ID header (INFR-02)."""
    response = await client.get("/test/app-error")
    assert "x-request-id" in response.headers


# INFR-08: PaginatedResponse model has required fields
def test_paginated_response_model():
    """PaginatedResponse has items, total, limit, offset fields (INFR-08)."""
    from api.models.common import PaginatedResponse

    fields = PaginatedResponse.model_fields
    assert "items" in fields
    assert "total" in fields
    assert "limit" in fields
    assert "offset" in fields


@pytest.mark.asyncio
async def test_validation_error_no_input_leak(client):
    """SEC-09: Validation errors must not echo user input or expose file paths."""
    resp = await client.post(
        "/api/v1/agents/register",
        json={
            "agent_name": "",  # violates min_length=1
            "model": "test",
            "owner_handle": "test",
        },
    )
    assert resp.status_code == 422
    data = resp.json()
    assert data["code"] == "VALIDATION_ERROR"
    assert "details" in data
    # Must not contain file paths
    response_text = str(data)
    assert "/home/" not in response_text
    assert "api/" not in response_text
