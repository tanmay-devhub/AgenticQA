"""Seeded-bug target: closed-interval intersection check.

Branchy comparison logic where a single ``<`` vs ``<=`` swap changes behavior
at the closed endpoints. Ideal shape for mutmut to surface real gaps.
"""

from __future__ import annotations


def intervals_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Return True iff the CLOSED intervals ``[a[0], a[1]]`` and ``[b[0], b[1]]``
    share at least one point.

    Rules:
        - Both intervals must be well-formed (``lo <= hi``). Otherwise raise
          ``ValueError`` -- silently returning False would hide a real bug.
        - Touching at a single endpoint counts as overlap:
          ``intervals_overlap((1, 5), (5, 9))`` -> True.
        - Fully disjoint returns False:
          ``intervals_overlap((1, 4), (5, 9))`` -> False.
        - Non-tuple / wrong-length inputs raise ``TypeError``.
    """
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

    return a[0] <= b[1] and b[0] <= a[1]
