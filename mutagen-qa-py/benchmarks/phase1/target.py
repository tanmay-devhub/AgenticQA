"""Phase 1 target: a tiny range-spec parser with real branching + edge cases.

Deliberately compact so mutmut finishes fast, but rich enough to expose:
    - comparison mutations (< vs <=),
    - arithmetic mutations (+1 vs -1 vs *2),
    - constant mutations (0 vs 1),
    - exception-path mutations (raise vs no-raise).
"""

from __future__ import annotations


def parse_range(spec: str) -> range:
    """Parse a range spec like ``"3-7"`` into ``range(3, 8)`` (end-inclusive).

    Rules:
        - ``"3-7"`` -> ``range(3, 8)``  (both ends inclusive in the spec).
        - ``"5"``   -> ``range(5, 6)``  (single value).
        - Whitespace around the value(s) is stripped.
        - Empty string or all-whitespace -> ``ValueError``.
        - Non-integer tokens -> ``ValueError``.
        - Negative numbers are allowed: ``"-3--1"`` -> ``range(-3, 0)``.
          (Split on the FIRST ``-`` that isn't at position 0.)
        - Reversed range (``"7-3"``) -> ``ValueError``.
        - More than one separator (``"1-2-3"``) -> ``ValueError``.
    """
    if not isinstance(spec, str):
        raise TypeError("spec must be a str")

    s = spec.strip()
    if not s:
        raise ValueError("empty spec")

    # Find the separator: first '-' that isn't at index 0.
    sep_idx = -1
    for i in range(1, len(s)):
        if s[i] == "-":
            sep_idx = i
            break

    if sep_idx == -1:
        # Single value.
        try:
            n = int(s)
        except ValueError as e:
            raise ValueError(f"not an integer: {s!r}") from e
        return range(n, n + 1)

    left = s[:sep_idx]
    right = s[sep_idx + 1 :]

    # Reject additional separators inside the right half (e.g. "1-2-3"),
    # while still allowing a leading '-' on the right (e.g. "-3--1").
    if right.startswith("-"):
        rest = right[1:]
    else:
        rest = right
    if "-" in rest:
        raise ValueError(f"too many separators: {spec!r}")

    try:
        lo = int(left)
        hi = int(right)
    except ValueError as e:
        raise ValueError(f"non-integer bound in {spec!r}") from e

    if lo > hi:
        raise ValueError(f"reversed range: {lo} > {hi}")

    return range(lo, hi + 1)
