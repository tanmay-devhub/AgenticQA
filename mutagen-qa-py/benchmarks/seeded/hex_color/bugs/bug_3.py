"""Seeded bug: green/blue slice indices off by one. `hex_to_rgb('#ff8800')`
returns (255, 0, 0) instead of (255, 136, 0)."""

from __future__ import annotations


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    if not isinstance(color, str):
        raise TypeError("color must be a str")
    s = color[1:] if color.startswith("#") else color
    if len(s) == 3:
        s = s[0] * 2 + s[1] * 2 + s[2] * 2
    if len(s) != 6:
        raise ValueError(f"expected 3 or 6 hex digits, got {len(s)}: {color!r}")
    try:
        r = int(s[0:2], 16)
        # BUG: g pulls indices 3:5 instead of 2:4 (skips a byte).
        g = int(s[3:5], 16)
        b = int(s[4:6], 16)
    except ValueError as e:
        raise ValueError(f"non-hex character in {color!r}") from e
    return (r, g, b)
