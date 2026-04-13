"""User account management."""


class UserService:
    """Manages user accounts.

    Stores users in an internal dict keyed by user_id. Depends on AuditLogger
    from src.audit to record sensitive operations.
    """

    def __init__(self) -> None:
        self._users: dict[str, dict] = {}

    def create_user(self, user_id: str, name: str) -> None:
        """Add a new user to the service."""
        self._users[user_id] = {"id": user_id, "name": name}

    def get_user(self, user_id: str) -> dict | None:
        """Return the user record, or None if not found."""
        return self._users.get(user_id)
