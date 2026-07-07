"""Second demo target: numeric clamp.

Behavior derivable in one glance (LLM won't hallucinate outputs), rich
comparison + branching so mutmut has plenty to mutate, and an error path
that's message-independent.
"""

from __future__ import annotations


def clamp(v: float, lo: float, hi: float) -> float:
    """Return ``v`` bounded to ``[lo, hi]``.

    Rules:
        - ``lo > hi`` -> ``ValueError``.
        - ``v < lo`` -> ``lo``.
        - ``v > hi`` -> ``hi``.
        - Otherwise -> ``v``.
        - ``lo == hi`` is legal; the only valid output is that value.
        - Boolean inputs are treated as int (Python semantics).
    """
    if lo > hi:
        raise ValueError("lo must be <= hi")
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v
