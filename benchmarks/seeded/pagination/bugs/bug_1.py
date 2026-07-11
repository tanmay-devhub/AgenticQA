"""Seeded bug: start uses ``page * per_page`` instead of ``(page-1) * per_page``.
`page_slice(100, 1, 10)` returns (10, 20) instead of (0, 10)."""

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

    # BUG: forgot the -1 on page.
    start = page * per_page
    if start >= total:
        return (total, total)
    end = start + per_page
    if end > total:
        end = total
    return (start, end)
