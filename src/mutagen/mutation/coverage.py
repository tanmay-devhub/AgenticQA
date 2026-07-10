"""Read the pytest-cov JSON report and expose missing lines per file.

Coverage is a *secondary* signal for the planner: it can't beat the mutation
score (which measures actual bug-catching power), but it flags branches the
tests never even executed. A survivor on an uncovered line is a stronger
`real_gap` than one on a covered line where the test exists but doesn't
observe the mutation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FileCoverage:
    filename: str
    executed_lines: list[int]
    missing_lines: list[int]

    @property
    def line_rate(self) -> float:
        total = len(self.executed_lines) + len(self.missing_lines)
        if total == 0:
            return 1.0
        return len(self.executed_lines) / total


def load_coverage(workdir: Path) -> dict[str, FileCoverage]:
    """Return {basename -> FileCoverage}. Missing / malformed report -> empty dict."""
    p = workdir / "coverage.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, FileCoverage] = {}
    files = data.get("files") or {}
    for path, payload in files.items():
        # coverage.py's top-level `executed_lines` / `missing_lines` are the
        # per-file line lists we want. NB: `summary.missing_lines` is a COUNT
        # (int), not a list -- don't try to read it here.
        fc = FileCoverage(
            filename=path,
            executed_lines=sorted(payload.get("executed_lines") or []),
            missing_lines=sorted(payload.get("missing_lines") or []),
        )
        # Key by basename so callers don't need to worry about relative-path variants.
        out[Path(path).name] = fc
    return out
