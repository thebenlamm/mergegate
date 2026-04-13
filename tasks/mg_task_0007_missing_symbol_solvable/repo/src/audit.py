"""Audit logging for sensitive operations."""


class AuditLogger:
    """Append-only log of sensitive actions.

    Events are stored in a class-level list. Use log_event() to record
    a new event. Tests may inspect and reset the log via _events and reset().
    """

    _events: list = []

    @classmethod
    def log_event(cls, actor: str, action: str, target: str) -> None:
        """Record an audit event.

        Args:
            actor: The identifier of the user or system performing the action.
            action: A short string describing what happened (e.g. "user.deleted").
            target: The identifier of the resource the action was performed on.
        """
        cls._events.append({"actor": actor, "action": action, "target": target})

    @classmethod
    def reset(cls) -> None:
        """Clear all recorded events. Intended for test setup."""
        cls._events = []
