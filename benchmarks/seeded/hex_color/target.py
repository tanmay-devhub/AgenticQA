"""Seeded-bug target: hex color -> (r, g, b) tuple.

Small enough for mutmut to finish fast, with three classes of bugs latent in
real implementations: case handling, shorthand expansion, and leading '#'.
"""

from __future__ import annotations


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Parse a hex color like ``"#a1b2c3"`` into an ``(r, g, b)`` tuple.

    Rules:
        - Leading ``#`` is optional. ``"a1b2c3"`` and ``"#a1b2c3"`` both work.
        - Case-insensitive: ``"#A1B2C3"`` and ``"#a1b2c3"`` are equivalent.
        - Three-digit shorthand: ``"#abc"`` -> ``(0xaa, 0xbb, 0xcc)``.
        - Any other length -> ``ValueError``.
        - Non-hex characters -> ``ValueError``.
        - Non-str input -> ``TypeError``.
    """
    if not isinstance(color, str):
        raise TypeError("color must be a str")
    s = color[1:] if color.startswith("#") else color
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
