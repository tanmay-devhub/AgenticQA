"""Seeded bug: day-zero accepted. `is_valid_date(2024, 3, 0)` returns True."""

from __future__ import annotations

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def is_valid_date(y: int, m: int, d: int) -> bool:
    for val in (y, m, d):
        if isinstance(val, bool) or not isinstance(val, int):
            return False
    if y < 1:
        return False
    if m < 1 or m > 12:
        return False
    # BUG: dropped the `if d < 1: return False` guard.
    max_d = _DAYS_IN_MONTH[m - 1]
    if m == 2 and y % 4 == 0 and (y % 100 != 0 or y % 400 == 0):
        max_d = 29
    return d <= max_d
