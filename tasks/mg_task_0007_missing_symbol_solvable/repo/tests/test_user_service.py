"""Tests for UserService.delete_user()."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audit import AuditLogger
from src.user_service import UserService


def setup_function(func):
    """Reset audit log between tests."""
    AuditLogger.reset()


def test_delete_user_removes_user_and_logs_event():
    svc = UserService()
    svc.create_user("u1", "Alice")
    svc.create_user("u2", "Bob")

    result = svc.delete_user("u1")

    assert result is True, "delete_user should return True on successful deletion"
    assert svc.get_user("u1") is None, "deleted user should no longer be retrievable"
    assert svc.get_user("u2") is not None, "other users should remain"

    # Verify the deletion was logged
    events = AuditLogger._events
    deletion_events = [e for e in events if e["action"] == "user.deleted"]
    assert len(deletion_events) == 1, f"expected 1 user.deleted event, got {len(deletion_events)}"
    assert deletion_events[0]["target"] == "u1", "deletion event target should be the user_id"


def test_delete_user_returns_false_when_user_not_found():
    svc = UserService()
    svc.create_user("u1", "Alice")

    result = svc.delete_user("nonexistent")

    assert result is False, "delete_user should return False when user not found"
    assert svc.get_user("u1") is not None, "existing users should remain"
