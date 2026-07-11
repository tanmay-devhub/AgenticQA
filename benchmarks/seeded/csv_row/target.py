"""Seeded-bug target: minimal RFC-4180-ish CSV row splitter.

Chosen for the seeded corpus because it exercises a state machine over a
string: a real bug can hide behind quote handling, escape logic, or the
final-field flush -- exactly the kind of thing where a suite that only
covers happy paths misses regressions.
"""

from __future__ import annotations


def parse_csv_row(row: str) -> list[str]:
    """Split one CSV row into fields.

    Rules:
        - Fields are comma-separated.
        - A field may be wrapped in double quotes; inside quotes ',' is literal.
        - Inside quotes, '""' is an escaped literal double quote.
        - Whitespace around unquoted fields is preserved as-is (no stripping).
        - Empty input '' -> [''] (one empty field).
        - Unterminated quoted field -> ValueError.
        - Non-str input -> TypeError.
    """
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

    if in_quotes:
        raise ValueError("unterminated quoted field")
    out.append("".join(buf))
    return out
