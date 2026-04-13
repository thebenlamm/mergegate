"""Tests for user creation handler with input validation."""

from src.handler import handle_create_user


def test_valid_user_creation():
    """Valid input should create a user."""
    result = handle_create_user(
        {
            "name": "Alice Smith",
            "email": "alice@example.com",
            "age": 30,
        }
    )
    assert result["status"] == 201
    assert result["user"]["name"] == "Alice Smith"


def test_valid_user_minimal():
    """Minimal valid input."""
    result = handle_create_user(
        {
            "name": "Bob",
            "email": "bob@test.io",
            "age": 0,
        }
    )
    assert result["status"] == 201
    assert result["user"]["age"] == 0


def test_empty_name_rejected():
    """Empty string name should be rejected."""
    result = handle_create_user(
        {
            "name": "",
            "email": "test@example.com",
            "age": 25,
        }
    )
    assert result["status"] == 400
    assert "error" in result


def test_email_without_at_rejected():
    """Email without @ symbol should be rejected."""
    result = handle_create_user(
        {
            "name": "Charlie",
            "email": "not-an-email",
            "age": 25,
        }
    )
    assert result["status"] == 400
    assert "error" in result


def test_negative_age_rejected():
    """Negative age should be rejected."""
    result = handle_create_user(
        {
            "name": "Diana",
            "email": "diana@example.com",
            "age": -1,
        }
    )
    assert result["status"] == 400
    assert "error" in result


def test_name_too_long_rejected():
    """Name over 100 characters should be rejected."""
    result = handle_create_user(
        {
            "name": "A" * 101,
            "email": "long@example.com",
            "age": 25,
        }
    )
    assert result["status"] == 400
    assert "error" in result
