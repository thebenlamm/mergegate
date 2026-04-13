"""Tests for calculator operations."""

from src.calculator import add, subtract, multiply


def test_add():
    assert add(2, 3) == 5


def test_add_negative():
    assert add(-1, -2) == -3


def test_subtract():
    assert subtract(10, 4) == 6


def test_multiply():
    assert multiply(3, 7) == 21


def test_multiply_by_zero():
    assert multiply(5, 0) == 0
