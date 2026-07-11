"""Seeded bug: page-past-end returns (start, start) instead of (total, total).
`page_slice(10, 5, 10)` returns (40, 40) instead of (10, 10) -- both are empty
slices, but downstream code that trusts start <= total will misbehave."""

from __future__ import annotations


def page_slice(total: int, page: int, per_page: int) -> tuple[int, int]:
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
        # BUG: returns (start, start) instead of clamping at total.
        return (start, start)
    end = start + per_page
    if end > total:
        end = total
    return (start, end)
