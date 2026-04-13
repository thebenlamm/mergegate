"""Tests for UUID7 generation (INFR-05).

Validates that generate_uuid7() produces valid, time-ordered UUID7 values
suitable for use as primary keys in agents and submissions tables.
"""

import time
from uuid import UUID

from api.utils import generate_uuid7


def test_uuid7_returns_uuid_object():
    """generate_uuid7() returns a stdlib uuid.UUID instance."""
    result = generate_uuid7()
    assert isinstance(result, UUID)


def test_uuid7_version_is_7():
    """Generated UUID has version 7."""
    result = generate_uuid7()
    assert result.version == 7


def test_uuid7_monotonic_ordering():
    """Multiple UUID7 values generated in sequence are time-ordered.

    UUID7 encodes millisecond timestamp in the high bits, so converting
    to int should produce monotonically increasing values (within the
    same millisecond, randomness may vary but the timestamp prefix
    ensures ordering across milliseconds).
    """
    ids = []
    for _ in range(3):
        ids.append(generate_uuid7())
        time.sleep(0.002)  # 2ms gap to cross millisecond boundary

    for i in range(len(ids) - 1):
        assert ids[i].int < ids[i + 1].int, (
            f"UUID7 values not monotonically ordered: {ids[i]} >= {ids[i + 1]}"
        )


def test_uuid7_uniqueness():
    """Rapid generation produces unique values."""
    ids = {generate_uuid7() for _ in range(100)}
    assert len(ids) == 100
