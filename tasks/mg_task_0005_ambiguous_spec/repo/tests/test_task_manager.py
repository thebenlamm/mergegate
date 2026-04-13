"""Tests for TaskManager with priority sorting."""

from src.task_manager import TaskManager


def test_add_task():
    """Adding a task stores it."""
    tm = TaskManager()
    tm.add_task("Write docs", priority=3)
    tasks = tm.get_tasks()
    assert len(tasks) == 1
    assert tasks[0]["name"] == "Write docs"


def test_get_tasks_insertion_order():
    """get_tasks returns tasks in insertion order."""
    tm = TaskManager()
    tm.add_task("A", priority=1)
    tm.add_task("B", priority=5)
    tm.add_task("C", priority=3)
    names = [t["name"] for t in tm.get_tasks()]
    assert names == ["A", "B", "C"]


def test_count():
    """Count reflects number of tasks added."""
    tm = TaskManager()
    assert tm.count() == 0
    tm.add_task("X", priority=1)
    tm.add_task("Y", priority=2)
    assert tm.count() == 2


def test_sorted_returns_all_tasks():
    """Sorted result contains all tasks."""
    tm = TaskManager()
    tm.add_task("Low", priority=1)
    tm.add_task("High", priority=10)
    tm.add_task("Mid", priority=5)
    sorted_tasks = tm.get_sorted_tasks()
    assert len(sorted_tasks) == 3
    names = {t["name"] for t in sorted_tasks}
    assert names == {"Low", "High", "Mid"}


def test_sorted_is_consistent():
    """Calling get_sorted_tasks twice returns the same order."""
    tm = TaskManager()
    tm.add_task("A", priority=2)
    tm.add_task("B", priority=8)
    tm.add_task("C", priority=5)
    first = tm.get_sorted_tasks()
    second = tm.get_sorted_tasks()
    assert first == second


def test_sorted_descending_interpretation():
    """If higher number = higher priority, 10 comes before 1."""
    tm = TaskManager()
    tm.add_task("Urgent", priority=10)
    tm.add_task("Low", priority=1)
    tm.add_task("Medium", priority=5)
    sorted_tasks = tm.get_sorted_tasks()
    priorities = [t["priority"] for t in sorted_tasks]
    assert priorities == [10, 5, 1], "Descending interpretation: higher number = higher priority"


def test_sorted_does_not_mutate_original():
    """Sorting should not change the original task list order."""
    tm = TaskManager()
    tm.add_task("A", priority=3)
    tm.add_task("B", priority=1)
    tm.add_task("C", priority=2)
    _ = tm.get_sorted_tasks()
    names = [t["name"] for t in tm.get_tasks()]
    assert names == ["A", "B", "C"]
