"""Tests for the Cache class with TTL expiration."""

from unittest.mock import patch

from src.cache import Cache


def test_set_and_get_basic():
    """Basic set/get works for non-expired items."""
    cache = Cache()
    cache.set("key1", "value1", ttl_seconds=60)
    assert cache.get("key1") == "value1"


def test_get_missing_key_returns_none():
    """Getting a key that was never set returns None."""
    cache = Cache()
    assert cache.get("nonexistent") is None


def test_expired_item_returns_none():
    """Items past their TTL should return None."""
    cache = Cache()
    with patch("src.cache.time") as mock_time:
        mock_time.monotonic.return_value = 100.0
        cache.set("key1", "value1", ttl_seconds=10)

        # Jump well past expiry
        mock_time.monotonic.return_value = 115.0
        assert cache.get("key1") is None


def test_ttl_boundary_exact_expiry():
    """Item at exactly its TTL boundary should be expired."""
    cache = Cache()
    with patch("src.cache.time") as mock_time:
        mock_time.monotonic.return_value = 100.0
        cache.set("key1", "value1", ttl_seconds=10)

        # Exactly at expiry boundary: 100 + 10 = 110
        mock_time.monotonic.return_value = 110.0
        result = cache.get("key1")
        assert result is None, "Item at exact TTL boundary should be expired, got value instead"


def test_cleanup_removes_expired_and_boundary():
    """Cleanup should remove items at or past their TTL."""
    cache = Cache()
    with patch("src.cache.time") as mock_time:
        mock_time.monotonic.return_value = 100.0
        cache.set("a", 1, ttl_seconds=5)
        cache.set("b", 2, ttl_seconds=10)
        cache.set("c", 3, ttl_seconds=20)

        # At t=110, 'a' (expires 105) and 'b' (expires 110, boundary) should go
        mock_time.monotonic.return_value = 110.0
        removed = cache.cleanup()
        assert removed == 2
        assert cache.size() == 1
        assert cache.get("c") == 3
