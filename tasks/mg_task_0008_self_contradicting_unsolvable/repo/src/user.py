"""User record factory."""


def User(name: str, extra_data: dict) -> dict:
    """Create a new user record as a dict."""
    return {"name": name, "extra_data": extra_data}
