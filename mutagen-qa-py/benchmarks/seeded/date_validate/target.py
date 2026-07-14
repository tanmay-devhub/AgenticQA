"""Seeded-bug target: Gregorian date validator.

Branchy validation with the classic leap-year gotcha: divisible by 4,
except centuries not divisible by 400 (1900 not leap; 2000 leap). Chosen
for the corpus because a happy-path suite that only tests 2020/2024 misses
the century rule entirely -- exactly the kind of gap mutmut surfaces and
that a T2/T3 pass should close.
"""

from __future__ import annotations

_DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def is_valid_date(y: int, m: int, d: int) -> bool:
    """Return True iff ``(y, m, d)`` is a valid proleptic-Gregorian date, ``y >= 1``.

    Rules:
        - All three args must be ``int`` and not ``bool``.
        - ``y >= 1``, ``1 <= m <= 12``, ``1 <= d <= days_in_month(y, m)``.
        - Leap year := ``y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)``.
    """
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
    if m == 2 and y % 4 == 0 and (y % 100 != 0 or y % 400 == 0):
        max_d = 29
    return d <= max_d
