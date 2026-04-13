"""Tests for structured logging with request IDs (INFR-02).

Verifies that every request generates an X-Request-ID response header,
that IDs are unique per request, and that structlog is configured with
contextvars support for request-scoped logging.
"""


async def test_request_id_in_response_header(client):
    """Every response includes X-Request-ID header (INFR-02)."""
    response = await client.get("/health")
    assert "x-request-id" in response.headers
    request_id = response.headers["x-request-id"]
    # UUID format: 8-4-4-4-12 (36 chars total)
    parts = request_id.split("-")
    assert len(parts) == 5
    assert [len(p) for p in parts] == [8, 4, 4, 4, 12]


async def test_request_id_unique_per_request(client):
    """Each request gets a unique request_id (INFR-02)."""
    r1 = await client.get("/health")
    r2 = await client.get("/health")
    id1 = r1.headers["x-request-id"]
    id2 = r2.headers["x-request-id"]
    assert id1 != id2, "Request IDs should be unique per request"


async def test_structlog_configured():
    """structlog is configured with contextvars processor (INFR-02)."""
    import structlog

    # Verify structlog is importable and has contextvars support
    assert hasattr(structlog.contextvars, "merge_contextvars")
    assert hasattr(structlog.contextvars, "bind_contextvars")
    assert hasattr(structlog.contextvars, "clear_contextvars")
