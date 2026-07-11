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
from mutagen.eval import benchmark as benchmark_mod
from mutagen.eval.benchmark import discover_targets, run_benchmark, score_seeded_bugs
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


def test_discover_targets_ignores_bugs_dir(tmp_path):
    """Buggy variants live at target/bugs/bug_*.py and must NOT be treated as
    independent benchmark targets."""
    root = tmp_path / "seeded"
    tgt_dir = root / "alpha"
    (tgt_dir / "bugs").mkdir(parents=True)
    (tgt_dir / "target.py").write_text(TARGET_SRC, encoding="utf-8")
    (tgt_dir / "bugs" / "target.py").write_text(TARGET_SRC, encoding="utf-8")

    found = discover_targets(root)
    assert len(found) == 1
    assert "bugs" not in found[0].parts


def test_score_seeded_bugs_measures_catch_rate(tmp_path):
    """Direct test of the swap-and-rerun scoring: no LLM, no loop.

    We hand-write a target, a matching one-line test, and two buggy variants:
    one the test catches, one it does not. Then confirm the scorer reports
    exactly which bugs were caught and restores the clean target at the end.
    """
    # Set up target folder with bugs alongside.
    tgt_dir = tmp_path / "adder"
    (tgt_dir / "bugs").mkdir(parents=True)
    (tgt_dir / "target.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    # bug_1 flips the sign; a==1, b==2 -> clean returns 3, buggy returns -1.
    # bug_2 hard-codes the answer for that single case; the test cannot see it.
    (tgt_dir / "bugs" / "bug_1.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    (tgt_dir / "bugs" / "bug_2.py").write_text(
        "def add(a, b):\n    return 3\n", encoding="utf-8"
    )
    (tgt_dir / "bugs.json").write_text(
        '{"bug_1.py": "sign flipped", "bug_2.py": "hardcoded to 3"}',
        encoding="utf-8",
    )

    # Simulate the workdir the loop would leave behind.
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "target.py").write_text(
        (tgt_dir / "target.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    # A single test: add(1, 2) == 3. Catches bug_1 (returns -1), misses bug_2.
    (workdir / "test_generated.py").write_text(
        "from target import add\n\ndef test_add(): assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    scores = score_seeded_bugs(tgt_dir / "target.py", workdir)

    assert len(scores) == 2
    by_id = {s.bug_id: s for s in scores}
    assert by_id["bug_1"].caught is True
    assert by_id["bug_2"].caught is False
    assert by_id["bug_1"].description == "sign flipped"
    # Clean target must be restored so the workdir is reusable.
    assert (workdir / "target.py").read_text(encoding="utf-8") == \
        "def add(a, b):\n    return a + b\n"


def test_score_seeded_bugs_returns_empty_when_no_corpus(tmp_path):
    """Targets without a bugs/ folder or bugs.json return an empty list --
    the loop still passes through unaffected."""
    tgt_dir = tmp_path / "plain"
    tgt_dir.mkdir()
    (tgt_dir / "target.py").write_text(TARGET_SRC, encoding="utf-8")
    workdir = tmp_path / "wd"
    workdir.mkdir()
    (workdir / "target.py").write_text(TARGET_SRC, encoding="utf-8")

    assert score_seeded_bugs(tgt_dir / "target.py", workdir) == []


def test_run_benchmark_populates_seeded_bugs(tmp_path, monkeypatch):
    """End-to-end: a target with a bug corpus records seeded_bugs on the entry
    and the aggregate mean_seeded_bug_catch_rate is populated."""
    root = tmp_path / "seeded"
    tgt_dir = root / "adder"
    (tgt_dir / "bugs").mkdir(parents=True)
    (tgt_dir / "target.py").write_text(TARGET_SRC, encoding="utf-8")
    (tgt_dir / "bugs" / "bug_1.py").write_text(TARGET_SRC, encoding="utf-8")
    (tgt_dir / "bugs.json").write_text('{"bug_1.py": "does nothing"}', encoding="utf-8")

    llm = FakeLLM(responses={"codegen": ["def test_a(): assert True\n"]})
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (_report(killed=1, survived=0), _ok()))
    # score_seeded_bugs calls run_pytest from benchmark_mod; stub it too so we
    # get a deterministic "caught? no" result (bug identical to clean).
    monkeypatch.setattr(benchmark_mod, "run_pytest", lambda *_a, **_kw: _ok())

    report = run_benchmark(
        targets=discover_targets(root),
        workdir_root=tmp_path / "runs",
        cfg=AppConfig(),
        llm=llm,
        max_rounds=1,
    )

    assert len(report.entries) == 1
    entry = report.entries[0]
    assert len(entry.seeded_bugs) == 1
    # Our stub reports pytest OK on the buggy variant -> bug NOT caught.
    assert entry.seeded_bugs[0].caught is False
    assert entry.seeded_bug_catch_rate == 0.0
    assert report.mean_seeded_bug_catch_rate == 0.0
