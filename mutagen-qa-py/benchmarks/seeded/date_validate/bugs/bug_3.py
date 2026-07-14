"""Seeded bug: month upper bound off-by-one. `is_valid_date(2024, 13, 1)`
returns True (accepts month 13)."""

from __future__ import annotations

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def is_valid_date(y: int, m: int, d: int) -> bool:
    for val in (y, m, d):
        if isinstance(val, bool) or not isinstance(val, int):
            return False
    if y < 1:
        return False
    # BUG: m > 12 relaxed to m > 13, so month 13 slips through -- and then
    # _DAYS_IN_MONTH[12] indexes off the end and raises IndexError.
    if m < 1 or m > 13:
        return False
    if d < 1:
        return False
    max_d = _DAYS_IN_MONTH[m - 1]
    if m == 2 and y % 4 == 0 and (y % 100 != 0 or y % 400 == 0):
        max_d = 29
    return d <= max_d
