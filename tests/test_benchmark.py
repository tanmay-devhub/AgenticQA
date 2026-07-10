"""Tests for the benchmark harness.

Uses the same monkeypatched pytest/mutmut approach as loop orchestration tests
so it stays fast and hermetic.
"""

from __future__ import annotations

import json
from pathlib import Path

from mutagen.agent import loop as loop_mod
from mutagen.agent.testing import FakeLLM
from mutagen.config import AppConfig
from mutagen.eval.benchmark import discover_targets, run_benchmark
from mutagen.mutation.report import MutationReport
from mutagen.sandbox.executor import RunResult


TARGET_SRC = "def add(a, b):\n    return a + b\n"


def _ok() -> RunResult:
    return RunResult(returncode=0, stdout="", stderr="", timed_out=False)


def _report(killed: int, survived: int) -> MutationReport:
    return MutationReport(total=killed + survived, killed=killed, survived=survived)


def _make_bench_tree(tmp_path: Path) -> Path:
    root = tmp_path / "benches"
    for name in ("alpha", "beta"):
        d = root / name
        d.mkdir(parents=True)
        (d / "target.py").write_text(TARGET_SRC, encoding="utf-8")
    return root


def test_discover_targets_finds_all(tmp_path):
    root = _make_bench_tree(tmp_path)
    found = discover_targets(root)
    assert len(found) == 2
    assert all(p.name == "target.py" for p in found)


def test_run_benchmark_aggregates_results(tmp_path, monkeypatch):
    root = _make_bench_tree(tmp_path)
    llm = FakeLLM(responses={"codegen": ["def test_a(): assert True\n"] * 4})
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    # Both targets: perfect kill rate.
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (_report(killed=3, survived=0), _ok()))

    report = run_benchmark(
        targets=discover_targets(root),
        workdir_root=tmp_path / "runs",
        cfg=AppConfig(),
        llm=llm,
        max_rounds=1,
    )

    assert len(report.entries) == 2
    assert report.mean_kill_rate == 1.0
    # benchmark.json is written for downstream tooling.
    payload = json.loads((tmp_path / "runs" / "benchmark.json").read_text(encoding="utf-8"))
    assert len(payload["entries"]) == 2


def test_run_benchmark_isolates_errors(tmp_path, monkeypatch):
    root = _make_bench_tree(tmp_path)
    # Give the FakeLLM only one response; the second target's tier1 call will raise.
    llm = FakeLLM(responses={"codegen": ["def test_a(): assert True\n"]})
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (_report(killed=1, survived=0), _ok()))

    report = run_benchmark(
        targets=discover_targets(root),
        workdir_root=tmp_path / "runs",
        cfg=AppConfig(),
        llm=llm,
        max_rounds=1,
    )

    assert len(report.entries) == 2
    ok = [e for e in report.entries if e.result is not None]
    failed = [e for e in report.entries if e.error is not None]
    assert len(ok) == 1
    assert len(failed) == 1
