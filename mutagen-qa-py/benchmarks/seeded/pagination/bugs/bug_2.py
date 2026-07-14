"""Seeded bug: end index never clamped to total. On the last partial page,
`page_slice(25, 3, 10)` returns (20, 30) instead of (20, 25)."""

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
        return (total, total)
    end = start + per_page
    # BUG: dropped the `if end > total: end = total` clamp.
    return (start, end)
