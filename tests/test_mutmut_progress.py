"""Live mutmut progress reader.

The reader parses the on-disk ``.mutmut-cache`` sqlite so the SSE stream can
report killed/survived/untested counts while mutmut is still running. We
fabricate the cache directly here -- no need to actually invoke mutmut.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mutagen.agent.testing import FakeLLM
from mutagen.web.jobs import JobRegistry


def _fake_cache(workdir: Path, counts: dict[str, int]) -> None:
    """Write a minimal ``.mutmut-cache`` with a Mutant table matching what
    mutmut itself would produce; the reader only ever cares about the
    (status, count) rollup."""
    workdir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(workdir / ".mutmut-cache")
    conn.execute("CREATE TABLE Mutant (id INTEGER PRIMARY KEY, status TEXT)")
    rows = []
    mid = 1
    for status, n in counts.items():
        for _ in range(n):
            rows.append((mid, status))
            mid += 1
    conn.executemany("INSERT INTO Mutant (id, status) VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


def _registry(tmp_path: Path) -> JobRegistry:
    return JobRegistry(tmp_path / "runs", llm_factory=lambda: FakeLLM())


def test_read_mutmut_counts_returns_none_when_cache_missing(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    assert reg._read_mutmut_counts(tmp_path / "nope") is None


def test_read_mutmut_counts_rolls_up_by_status(tmp_path: Path) -> None:
    wd = tmp_path / "wd"
    _fake_cache(wd, {"ok_killed": 7, "bad_survived": 2, "untested": 11})
    reg = _registry(tmp_path)
    got = reg._read_mutmut_counts(wd)
    assert got == {"killed": 7, "survived": 2, "untested": 11, "total": 20}


def test_read_mutmut_counts_treats_unknown_status_as_untracked(tmp_path: Path) -> None:
    """Only the three status buckets we surface should populate their fields;
    anything else (skipped, timeout, suspicious) still contributes to
    ``total`` so the denominator stays honest."""
    wd = tmp_path / "wd"
    _fake_cache(wd, {"ok_killed": 3, "bad_timeout": 1, "bad_suspicious": 2})
    reg = _registry(tmp_path)
    got = reg._read_mutmut_counts(wd)
    assert got["killed"] == 3
    assert got["survived"] == 0
    assert got["untested"] == 0
    assert got["total"] == 6


def test_read_mutmut_counts_survives_locked_cache(tmp_path: Path) -> None:
    """Read-only sqlite opens never block mutmut's writes; even if the cache
    momentarily has a busy writer, we should surface None rather than crash."""
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / ".mutmut-cache").write_bytes(b"not a real sqlite db")
    reg = _registry(tmp_path)
    assert reg._read_mutmut_counts(wd) is None
