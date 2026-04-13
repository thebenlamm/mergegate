"""Regression tests for existing config parser behavior."""

from src.config_parser import parse_config


def test_flat_key_value():
    """Flat key=value pairs without sections."""
    text = "host=localhost\nport=5432\ndebug=true"
    result = parse_config(text)
    assert result == {"host": "localhost", "port": "5432", "debug": "true"}


def test_single_section():
    """Single section with keys."""
    text = "[database]\nhost=localhost\nport=5432"
    result = parse_config(text)
    assert result == {"database": {"host": "localhost", "port": "5432"}}


def test_mixed_flat_and_section():
    """Flat keys followed by a section."""
    text = "app_name=myapp\n\n[database]\nhost=localhost"
    result = parse_config(text)
    assert result == {"app_name": "myapp", "database": {"host": "localhost"}}
