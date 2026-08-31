"""Shared grade type and helpers used across routing and infra."""

from __future__ import annotations

from typing import Literal

Grade = Literal["green", "yellow", "red"]

GRADE_RANK = {"green": 0, "yellow": 1, "red": 2}


def worst_grade(*grades: Grade) -> Grade:
    return max(grades, key=lambda g: GRADE_RANK.get(g, 2))


def grade_lower(value: float, green: float, yellow: float) -> Grade:
    if value <= green:
        return "green"
    if value <= yellow:
        return "yellow"
    return "red"


def grade_higher(value: float, green: float, yellow: float) -> Grade:
    if value >= green:
        return "green"
    if value >= yellow:
        return "yellow"
    return "red"
