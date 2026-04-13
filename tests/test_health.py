"""Tests for GET /health endpoint (INFR-01, INFR-02).

INFR-01: GET /health returns 200 with status, timestamp, and version fields.
INFR-02: Every response includes an X-Request-ID header.
"""


async def test_health_returns_200(client):
    """GET /health returns 200 with required fields (INFR-01)."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "timestamp" in data
    assert "version" in data
    assert data["status"] == "ok"


async def test_health_response_shape(client):
    """GET /health returns all expected fields (INFR-01)."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) >= {"status", "timestamp", "version", "database"}
    assert data["database"] == "connected"


async def test_health_has_request_id(client):
    """Every response includes X-Request-ID header (INFR-02)."""
    response = await client.get("/health")
    assert "x-request-id" in response.headers
    # Verify it's a valid UUID format (36 chars with dashes in 8-4-4-4-12 pattern)
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 36
    assert request_id.count("-") == 4
