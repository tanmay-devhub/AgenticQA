"""Seeded bug: leading '#' never stripped. `hex_to_rgb('#a1b2c3')` raises
ValueError because the '#' shifts every slice by one and int('#a', 16) fails."""

from __future__ import annotations


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    if not isinstance(color, str):
        raise TypeError("color must be a str")
    # BUG: strip step removed; '#a1b2c3' has length 7 or shifts slices.
    s = color
    if len(s) == 3:
        s = s[0] * 2 + s[1] * 2 + s[2] * 2
    if len(s) != 6:
        raise ValueError(f"expected 3 or 6 hex digits, got {len(s)}: {color!r}")
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
    except ValueError as e:
        raise ValueError(f"non-hex character in {color!r}") from e
    return (r, g, b)
