"""Simple key-value cache with TTL expiration."""

import time


class Cache:
    def __init__(self):
        self._store = {}  # key -> (value, expire_time)

    def set(self, key: str, value, ttl_seconds: float) -> None:
        """Store a value with a TTL in seconds."""
        expire_time = time.monotonic() + ttl_seconds
        self._store[key] = (value, expire_time)

    def get(self, key: str):
        """Retrieve a value if it exists and hasn't expired."""
        if key not in self._store:
            return None
        value, expire_time = self._store[key]
        now = time.monotonic()
        if now > expire_time:  # BUG: should be >=
            del self._store[key]
            return None
        return value

    def cleanup(self) -> int:
        """Remove all expired entries. Returns count of removed items."""
        now = time.monotonic()
        expired_keys = [
            k
            for k, (_, exp) in self._store.items()
            if now > exp  # BUG: should be >=
        ]
        for k in expired_keys:
            del self._store[k]
        return len(expired_keys)

    def size(self) -> int:
        """Return number of items in cache (including expired but not yet cleaned)."""
        return len(self._store)
