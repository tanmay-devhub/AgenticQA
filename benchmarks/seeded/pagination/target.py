"""Seeded-bug target: pagination index math.

Numeric shape with real edge cases (page past the end, exact-fit final page,
per_page > total). Chosen for the corpus because it looks trivial but hides
a family of off-by-one bugs that survive naive happy-path tests.
"""

from __future__ import annotations


def page_slice(total: int, page: int, per_page: int) -> tuple[int, int]:
    """Return ``(start, end)`` for the requested page.

    Rules:
        - Pages are 1-indexed; ``page=1`` starts at index 0.
        - ``end`` is EXCLUSIVE and clamped at ``total``.
        - If the page is past the last item, return ``(total, total)``.
        - ``total`` must be ``>= 0``; ``page`` and ``per_page`` must be ``>= 1``.
        - Bools are rejected as non-int (they'd silently work as 0/1 otherwise).
    """
    for name, val in (("total", total), ("page", page), ("per_page", per_page)):
        if isinstance(val, bool) or not isinstance(val, int):
            raise ValueError(f"{name} must be int")
    if total < 0:
        raise ValueError("total must be >= 0")
    if page < 1:
        raise ValueError("page must be >= 1")
    if per_page < 1:
        raise ValueError("per_page must be >= 1")

    start = (page - 1) * per_page
    if start >= total:
        return (total, total)
    end = start + per_page
    if end > total:
        end = total
    return (start, end)
