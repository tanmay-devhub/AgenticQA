"""Seeded bug: 3-digit shorthand not expanded. `hex_to_rgb('#abc')` raises
ValueError instead of returning (0xaa, 0xbb, 0xcc)."""

from __future__ import annotations


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    if not isinstance(color, str):
        raise TypeError("color must be a str")
    s = color[1:] if color.startswith("#") else color
    # BUG: shorthand-expansion branch removed.
    if len(s) != 6:
        raise ValueError(f"expected 3 or 6 hex digits, got {len(s)}: {color!r}")
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
    except ValueError as e:
        raise ValueError(f"non-hex character in {color!r}") from e
    return (r, g, b)
