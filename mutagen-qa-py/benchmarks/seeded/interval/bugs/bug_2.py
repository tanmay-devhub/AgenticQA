"""Seeded bug: reversed-interval validation dropped.
`intervals_overlap((5,1), (0,10))` returns True (should raise ValueError)."""

from __future__ import annotations


def intervals_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    for name, iv in (("a", a), ("b", b)):
        if not isinstance(iv, tuple) or len(iv) != 2:
            raise TypeError(f"{name} must be a 2-tuple, got {iv!r}")
        lo, hi = iv
        if not isinstance(lo, int) or not isinstance(hi, int):
            raise TypeError(f"{name} endpoints must be int, got {iv!r}")
        if isinstance(lo, bool) or isinstance(hi, bool):
            raise TypeError(f"{name} endpoints must be int, not bool")
        # BUG: reversed-interval guard removed; downstream comparison silently
        # succeeds (since 5<=10 and 0<=1) and returns True.

    return a[0] <= b[1] and b[0] <= a[1]
