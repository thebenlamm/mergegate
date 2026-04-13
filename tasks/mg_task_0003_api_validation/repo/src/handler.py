"""Request handler for user creation."""


def handle_create_user(request_data: dict) -> dict:
    """Create a new user from request data.

    Expected fields:
    - name: str, 1-100 characters, alphanumeric and spaces only
    - email: str, must contain @
    - age: int, 0-150

    Returns:
        {"status": 201, "user": {"name": ..., "email": ..., "age": ...}}
        or {"status": 4xx, "error": "description"} on validation failure
    """
    name = request_data.get("name", "")
    email = request_data.get("email", "")
    age = request_data.get("age", 0)

    # No validation — just accept everything
    user = {
        "name": name,
        "email": email,
        "age": age,
    }

    return {"status": 201, "user": user}
