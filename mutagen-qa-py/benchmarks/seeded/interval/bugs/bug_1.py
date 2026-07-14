"""Seeded bug: touching endpoints not counted as overlap.
`intervals_overlap((1,5), (5,9))` returns False instead of True."""

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

    # BUG: strict inequality; closed intervals should use <=.
    return a[0] < b[1] and b[0] < a[1]
