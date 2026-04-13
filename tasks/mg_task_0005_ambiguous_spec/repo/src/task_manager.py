"""Task list manager with priority support."""


class TaskManager:
    def __init__(self):
        self._tasks = []

    def add_task(self, name: str, priority: int) -> None:
        """Add a task with a given priority (integer)."""
        self._tasks.append({"name": name, "priority": priority})

    def get_tasks(self) -> list[dict]:
        """Return all tasks in insertion order."""
        return list(self._tasks)

    def count(self) -> int:
        """Return number of tasks."""
        return len(self._tasks)

    def get_sorted_tasks(self) -> list[dict]:
        """Return tasks sorted by priority.

        Higher priority tasks should appear first.
        Not yet implemented.
        """
        raise NotImplementedError("Sorting by priority is not yet implemented")
