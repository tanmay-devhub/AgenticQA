"""Benchmark harness: run the loop against every target in a folder tree.

Given a root like ``benchmarks/``, walk it for ``target.py`` files, run
``run_loop`` against each, and aggregate kill rate, wall clock, token spend,
and stop reason into a single machine-readable summary. The harness only
depends on the public LoopResult API, so ablations (e.g. T1-only vs full-tier)
are just a matter of tweaking ``max_rounds`` at the call site.

Seeded-bug scoring: if a target folder contains ``bugs/bug_*.py`` and a
``bugs.json`` manifest, after the loop finishes we swap each buggy variant
into ``workdir/target.py`` and rerun the generated pytest suite. A bug is
"caught" iff pytest fails on the buggy variant. This measures what
mutation-kill-rate cannot: whether the suite catches *actual* wrong behavior
that a human would seed, not just synthetic mutations.
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
from mutagen.sandbox.executor import run_pytest


@dataclass
class SeededBugScore:
    bug_id: str
    description: str
    caught: bool  # pytest failed on the buggy target -> bug caught

    def to_dict(self) -> dict:
        return {"bug_id": self.bug_id, "description": self.description, "caught": self.caught}


@dataclass
class BenchmarkEntry:
    target: Path
    workdir: Path
    result: LoopResult | None
    error: str | None = None
    wall_clock_s: float = 0.0
    seeded_bugs: list[SeededBugScore] = field(default_factory=list)

    @property
    def seeded_bug_catch_rate(self) -> float | None:
        if not self.seeded_bugs:
            return None
        caught = sum(1 for s in self.seeded_bugs if s.caught)
        return caught / len(self.seeded_bugs)

    def to_dict(self) -> dict:
        return {
            "target": str(self.target),
            "workdir": str(self.workdir),
            "wall_clock_s": self.wall_clock_s,
            "error": self.error,
            "result": self.result.to_dict() if self.result else None,
            "seeded_bugs": [s.to_dict() for s in self.seeded_bugs],
            "seeded_bug_catch_rate": self.seeded_bug_catch_rate,
        }


@dataclass
class BenchmarkReport:
    entries: list[BenchmarkEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "mean_kill_rate": self.mean_kill_rate,
            "mean_seeded_bug_catch_rate": self.mean_seeded_bug_catch_rate,
        }

    @property
    def mean_kill_rate(self) -> float:
        rates = [
            e.result.final_report.kill_rate
            for e in self.entries
            if e.result and e.result.final_report is not None
        ]
        return sum(rates) / len(rates) if rates else 0.0

    @property
    def mean_seeded_bug_catch_rate(self) -> float | None:
        rates = [e.seeded_bug_catch_rate for e in self.entries if e.seeded_bug_catch_rate is not None]
        return sum(rates) / len(rates) if rates else None


def discover_targets(root: Path) -> list[Path]:
    """Every ``target.py`` under ``root`` (recursive). Sorted for reproducibility.

    Skips files under a ``bugs/`` directory so seeded-bug variants are not
    treated as separate benchmark targets.
    """
    out: list[Path] = []
    for p in root.rglob("target.py"):
        if "bugs" in p.parts:
            continue
        out.append(p)
    return sorted(out)


def score_seeded_bugs(target: Path, workdir: Path, *, timeout_s: int = 60) -> list[SeededBugScore]:
    """Swap each ``bugs/bug_*.py`` into the workdir target and rerun pytest.

    A seeded bug is "caught" iff pytest exits non-zero on the buggy target.
    We restore the clean target at the end even on exceptions so callers can
    reuse the workdir.
    """
    bugs_dir = target.parent / "bugs"
    manifest_path = target.parent / "bugs.json"
    if not bugs_dir.is_dir() or not manifest_path.is_file():
        return []

    try:
        descriptions = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        descriptions = {}

    workdir_target = workdir / "target.py"
    if not workdir_target.exists():
        return []
    clean_source = workdir_target.read_text(encoding="utf-8")

    scores: list[SeededBugScore] = []
    try:
        for bug_file in sorted(bugs_dir.glob("bug_*.py")):
            workdir_target.write_text(
                bug_file.read_text(encoding="utf-8"), encoding="utf-8"
            )
            # Coverage is off here: we only care about pass/fail on the buggy version.
            res = run_pytest(workdir, timeout_s=timeout_s)
            caught = res.returncode != 0
            desc = descriptions.get(bug_file.name, "")
            scores.append(SeededBugScore(bug_id=bug_file.stem, description=desc, caught=caught))
    finally:
        workdir_target.write_text(clean_source, encoding="utf-8")
    return scores


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

    After a successful loop, if the target has a seeded-bug corpus alongside
    (``bugs/`` + ``bugs.json``), score seeded-bug detection too.
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
            if result.pytest_ok:
                entry.seeded_bugs = score_seeded_bugs(target, wd)
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


@dataclass
class AblationConfig:
    label: str
    max_rounds: int


DEFAULT_ABLATIONS = (
    AblationConfig("T1-only", max_rounds=1),
    AblationConfig("T1+T2", max_rounds=2),
    AblationConfig("full-tier", max_rounds=3),
)


@dataclass
class AblationEntry:
    """One (target, config) cell in the ablation grid."""
    target: Path
    config: AblationConfig
    kill_rate: float | None
    seeded_bug_catch_rate: float | None
    tokens: int
    wall_clock_s: float
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "target": str(self.target),
            "config_label": self.config.label,
            "max_rounds": self.config.max_rounds,
            "kill_rate": self.kill_rate,
            "seeded_bug_catch_rate": self.seeded_bug_catch_rate,
            "tokens": self.tokens,
            "wall_clock_s": self.wall_clock_s,
            "error": self.error,
        }


@dataclass
class AblationReport:
    """A full grid of (target, config) results with per-config aggregates."""
    entries: list[AblationEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "per_config": {
                label: agg for label, agg in self.per_config_summary().items()
            },
        }

    def per_config_summary(self) -> dict[str, dict]:
        by_label: dict[str, list[AblationEntry]] = {}
        for e in self.entries:
            by_label.setdefault(e.config.label, []).append(e)
        out: dict[str, dict] = {}
        for label, group in by_label.items():
            kill_rates = [e.kill_rate for e in group if e.kill_rate is not None]
            seeded = [e.seeded_bug_catch_rate for e in group if e.seeded_bug_catch_rate is not None]
            tokens = [e.tokens for e in group]
            out[label] = {
                "mean_kill_rate": sum(kill_rates) / len(kill_rates) if kill_rates else None,
                "mean_seeded_bug_catch_rate": sum(seeded) / len(seeded) if seeded else None,
                "mean_tokens": sum(tokens) / len(tokens) if tokens else 0,
                "n_targets": len(group),
            }
        return out


def run_ablation(
    *,
    targets: Iterable[Path],
    workdir_root: Path,
    cfg: AppConfig,
    llm: LLM,
    configs: Iterable[AblationConfig] = DEFAULT_ABLATIONS,
) -> AblationReport:
    """Run each target under each config; return the (target x config) grid.

    Every (target, config) pair gets its own workdir so tests written under
    one config never leak into another's mutmut score. This is what makes the
    grid comparable: the only variable between two cells for the same target
    is the loop budget.
    """
    workdir_root.mkdir(parents=True, exist_ok=True)
    report = AblationReport()
    configs = list(configs)

    for target in targets:
        for cfg_row in configs:
            wd = workdir_root / f"{target.parent.name}_{target.stem}__{cfg_row.label}"
            t0 = time.monotonic()
            try:
                result = run_loop(
                    target=target, workdir=wd, cfg=cfg, llm=llm,
                    max_rounds=cfg_row.max_rounds,
                )
                kill_rate = (
                    result.final_report.kill_rate if result.final_report else None
                )
                usage = result.total_usage
                tokens = (
                    usage.codegen.prompt_tokens + usage.codegen.completion_tokens
                    + usage.planner.prompt_tokens + usage.planner.completion_tokens
                )
                seeded = None
                if result.pytest_ok:
                    scores = score_seeded_bugs(target, wd)
                    if scores:
                        caught = sum(1 for s in scores if s.caught)
                        seeded = caught / len(scores)
                entry = AblationEntry(
                    target=target, config=cfg_row,
                    kill_rate=kill_rate, seeded_bug_catch_rate=seeded,
                    tokens=tokens, wall_clock_s=time.monotonic() - t0,
                )
            except Exception as e:
                entry = AblationEntry(
                    target=target, config=cfg_row,
                    kill_rate=None, seeded_bug_catch_rate=None,
                    tokens=0, wall_clock_s=time.monotonic() - t0,
                    error=repr(e),
                )
            report.entries.append(entry)

    (workdir_root / "ablation.json").write_text(
        json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8"
    )
    return report
