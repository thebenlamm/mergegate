"""Pre-existing test for the User type. Must continue to pass after refactor."""

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.user import User


def test_user_is_dataclass():
    """After refactor, User must be a @dataclass."""
    assert dataclasses.is_dataclass(User), "User must be converted to a @dataclass"


def test_user_has_score():
    u = User(name="alice", extra_data={"score": 42, "level": 3})
    # Dataclass field access
    assert u.extra_data["score"] == 42
    assert u.extra_data["level"] == 3
    assert u.name == "alice"
