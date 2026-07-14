"""Seeded bug: leap rule reduces to ``y % 4 == 0``. `is_valid_date(1900, 2, 29)`
returns True instead of False; `is_valid_date(2100, 2, 29)` returns True too."""

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
    if d < 1:
        return False
    max_d = _DAYS_IN_MONTH[m - 1]
    # BUG: dropped the 100/400 rule.
    if m == 2 and y % 4 == 0:
        max_d = 29
    return d <= max_d
