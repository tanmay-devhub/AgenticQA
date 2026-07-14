"""Seeded bug: unterminated quoted field silently returns instead of raising.
`parse_csv_row('"abc')` returns ['abc'] instead of ValueError."""

from __future__ import annotations


def parse_csv_row(row: str) -> list[str]:
    if not isinstance(row, str):
        raise TypeError("row must be a str")

    out: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(row)
    in_quotes = False

    while i < n:
        c = row[i]
        if in_quotes:
            if c == '"':
                if i + 1 < n and row[i + 1] == '"':
                    buf.append('"')
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            buf.append(c)
            i += 1
        else:
            if c == '"':
                in_quotes = True
                i += 1
            elif c == ",":
                out.append("".join(buf))
                buf = []
                i += 1
            else:
                buf.append(c)
                i += 1

    # BUG: dropped the `if in_quotes: raise ValueError(...)` guard.
    out.append("".join(buf))
    return out
