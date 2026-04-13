"""Tests for nested section support using dot notation."""

from src.config_parser import parse_config


def test_nested_section_basic():
    """Dot-notation sections create nested dicts."""
    text = "[database.primary]\nhost=localhost\nport=5432"
    result = parse_config(text)
    assert result == {"database": {"primary": {"host": "localhost", "port": "5432"}}}


def test_nested_section_multiple():
    """Multiple nested sections under same parent."""
    text = "[database.primary]\nhost=db1.example.com\n\n[database.replica]\nhost=db2.example.com\n"
    result = parse_config(text)
    assert result == {
        "database": {
            "primary": {"host": "db1.example.com"},
            "replica": {"host": "db2.example.com"},
        }
    }


def test_nested_three_levels():
    """Three levels of nesting."""
    text = "[cloud.aws.s3]\nbucket=my-bucket\nregion=us-east-1"
    result = parse_config(text)
    assert result == {"cloud": {"aws": {"s3": {"bucket": "my-bucket", "region": "us-east-1"}}}}


def test_nested_mixed_with_flat_section():
    """Flat section and nested section coexist."""
    text = "[logging]\nlevel=INFO\n\n[database.primary]\nhost=localhost\n"
    result = parse_config(text)
    assert result == {
        "logging": {"level": "INFO"},
        "database": {"primary": {"host": "localhost"}},
    }


def test_nested_preserves_flat_keys():
    """Flat keys at top level survive alongside nested sections."""
    text = "app_name=myapp\n\n[server.http]\nport=8080\n"
    result = parse_config(text)
    assert result == {
        "app_name": "myapp",
        "server": {"http": {"port": "8080"}},
    }
