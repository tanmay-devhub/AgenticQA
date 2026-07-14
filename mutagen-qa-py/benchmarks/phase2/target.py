"""Phase 2 target: a small pricing function.

Different shape from `parse_range` (parser) and `clamp` (single-branch numeric):
this one has multiple branch arms, tier-based arithmetic, and a cap. Rich in
`comparison`, `arithmetic`, and `constant` mutations without touching strings.
"""

from __future__ import annotations


def discounted_price(cents: int, tier: int) -> int:
    """Return the discounted price in whole cents for a membership tier.

    Tiers:
        - 0: no discount, price unchanged.
        - 1: 10% off, rounded DOWN.
        - 2: 20% off, rounded DOWN.
        - 3: 25% off, rounded DOWN, then subtract a flat 500-cent bonus.
             The final price is clamped at 0 (never negative).
        - Any other tier -> ``ValueError``.

    Errors:
        - ``cents`` must be a non-negative int; otherwise ``ValueError``.
        - Boolean ``cents`` (True/False) is rejected because booleans are a
          common accidental-arg bug and we want mutmut to see the guard.
    """
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise ValueError("cents must be a non-negative int")
    if cents < 0:
        raise ValueError("cents must be a non-negative int")
    if tier == 0:
        return cents
    if tier == 1:
        return cents * 90 // 100
    if tier == 2:
        return cents * 80 // 100
    if tier == 3:
        after_pct = cents * 75 // 100
        after_bonus = after_pct - 500
        if after_bonus < 0:
            return 0
        return after_bonus
    raise ValueError(f"unknown tier: {tier}")
