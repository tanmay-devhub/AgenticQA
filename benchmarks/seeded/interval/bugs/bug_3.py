"""Seeded bug: only one side of the two-sided overlap check remains.
`intervals_overlap((10, 20), (0, 5))` returns True instead of False, because
`b[0] <= a[1]` (0 <= 20) still holds even though the intervals are disjoint."""

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
        if lo > hi:
            raise ValueError(f"{name} is reversed: {iv!r}")

    # BUG: only the b[0] <= a[1] side remains; a[0] <= b[1] deleted.
    return b[0] <= a[1]
