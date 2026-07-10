"""Benchmark harness: run the loop against every target in a folder tree.

Given a root like ``benchmarks/``, walk it for ``target.py`` files, run
``run_loop`` against each, and aggregate kill rate, wall clock, token spend,
and stop reason into a single machine-readable summary. The harness only
depends on the public LoopResult API, so ablations (e.g. T1-only vs full-tier)
are just a matter of tweaking ``max_rounds`` at the call site.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from mutagen.agent.llm import LLM
from mutagen.agent.loop import LoopResult, run_loop
from mutagen.config import AppConfig


@dataclass
class BenchmarkEntry:
    target: Path
    workdir: Path
    result: LoopResult | None
    error: str | None = None
    wall_clock_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "target": str(self.target),
            "workdir": str(self.workdir),
            "wall_clock_s": self.wall_clock_s,
            "error": self.error,
            "result": self.result.to_dict() if self.result else None,
        }


@dataclass
class BenchmarkReport:
    entries: list[BenchmarkEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"entries": [e.to_dict() for e in self.entries]}

    @property
    def mean_kill_rate(self) -> float:
        rates = [
            e.result.final_report.kill_rate
            for e in self.entries
            if e.result and e.result.final_report is not None
        ]
        return sum(rates) / len(rates) if rates else 0.0


def discover_targets(root: Path) -> list[Path]:
    """Every ``target.py`` under ``root`` (recursive). Sorted for reproducibility."""
    return sorted(root.rglob("target.py"))


def run_benchmark(
    *,
    targets: Iterable[Path],
    workdir_root: Path,
    cfg: AppConfig,
    llm: LLM,
    max_rounds: int | None = None,
) -> BenchmarkReport:
    """Run the loop against each target, isolating workdirs, tolerating failures.

    A crash in one target does NOT abort the whole run; the entry is marked
    with ``error`` and the harness continues. This matches how benchmark grids
    are actually consumed downstream.
    """
    report = BenchmarkReport()
    workdir_root.mkdir(parents=True, exist_ok=True)

    for target in targets:
        wd = workdir_root / f"{target.parent.name}_{target.stem}"
        t0 = time.monotonic()
        try:
            result = run_loop(
                target=target, workdir=wd, cfg=cfg, llm=llm, max_rounds=max_rounds
            )
            entry = BenchmarkEntry(
                target=target, workdir=wd, result=result,
                wall_clock_s=time.monotonic() - t0,
            )
        except Exception as e:
            entry = BenchmarkEntry(
                target=target, workdir=wd, result=None, error=repr(e),
                wall_clock_s=time.monotonic() - t0,
            )
        report.entries.append(entry)

    (workdir_root / "benchmark.json").write_text(
        json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    return report
