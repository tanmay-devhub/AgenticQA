"""Ablation grid: run each target under multiple loop budgets and compare.

Same stub pattern as the benchmark tests -- monkeypatch the expensive calls
so we exercise the grid logic (per-config aggregation, error isolation)
without hitting mutmut / an LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

from mutagen.agent import loop as loop_mod
from mutagen.agent.testing import FakeLLM
from mutagen.config import AppConfig
from mutagen.eval import benchmark as benchmark_mod
from mutagen.eval.benchmark import (
    AblationConfig,
    discover_targets,
    run_ablation,
)
from mutagen.mutation.report import MutationReport
from mutagen.sandbox.executor import RunResult


TARGET_SRC = "def add(a, b):\n    return a + b\n"


def _ok() -> RunResult:
    return RunResult(returncode=0, stdout="", stderr="", timed_out=False)


def _report(killed: int, survived: int) -> MutationReport:
    return MutationReport(total=killed + survived, killed=killed, survived=survived)


def _make_tree(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    for name in ("alpha", "beta"):
        d = root / name
        d.mkdir(parents=True)
        (d / "target.py").write_text(TARGET_SRC, encoding="utf-8")
    return root


def test_run_ablation_produces_row_per_target_x_config(tmp_path, monkeypatch):
    root = _make_tree(tmp_path)
    # Enough codegen responses to satisfy 2 targets x 3 configs = 6 rounds
    # (each config runs at least one T1 shot); pad generously.
    llm = FakeLLM(responses={"codegen": ["def test_a(): assert True\n"] * 30})
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    # Bump kill rate slightly per call so the "escalation earns kills" story
    # is visible in the grid.
    kills = iter([5, 5, 6, 7, 5, 6, 7, 8, 5, 7, 9])
    monkeypatch.setattr(
        loop_mod, "run_mutmut",
        lambda **_kw: (_report(next(kills), 5), _ok()),
    )

    report = run_ablation(
        targets=discover_targets(root),
        workdir_root=tmp_path / "grid",
        cfg=AppConfig(),
        llm=llm,
        configs=[
            AblationConfig("T1-only", max_rounds=1),
            AblationConfig("full-tier", max_rounds=3),
        ],
    )

    # 2 targets x 2 configs = 4 grid entries.
    assert len(report.entries) == 4
    by_key = {(e.target.parent.name, e.config.label) for e in report.entries}
    assert by_key == {("alpha", "T1-only"), ("alpha", "full-tier"),
                      ("beta", "T1-only"), ("beta", "full-tier")}

    per_config = report.per_config_summary()
    assert set(per_config.keys()) == {"T1-only", "full-tier"}
    assert per_config["T1-only"]["n_targets"] == 2
    assert per_config["full-tier"]["n_targets"] == 2

    # ablation.json is written.
    ab_path = tmp_path / "grid" / "ablation.json"
    assert ab_path.exists()
    payload = json.loads(ab_path.read_text(encoding="utf-8"))
    assert "entries" in payload
    assert "per_config" in payload


def test_run_ablation_isolates_workdirs(tmp_path, monkeypatch):
    """Each (target, config) cell gets its own workdir so tests generated
    under T1-only never contaminate the full-tier cell's mutmut score."""
    root = _make_tree(tmp_path)
    llm = FakeLLM(responses={"codegen": ["def test_a(): assert True\n"] * 30})
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(
        loop_mod, "run_mutmut", lambda **_kw: (_report(5, 5), _ok()),
    )

    report = run_ablation(
        targets=discover_targets(root),
        workdir_root=tmp_path / "grid",
        cfg=AppConfig(),
        llm=llm,
        configs=[AblationConfig("T1-only", max_rounds=1),
                 AblationConfig("full-tier", max_rounds=3)],
    )

    workdirs = {(tmp_path / "grid" / f"{e.target.parent.name}_{e.target.stem}__{e.config.label}")
                for e in report.entries}
    assert len(workdirs) == 4
    for wd in workdirs:
        assert wd.is_dir()
        assert (wd / "run.json").exists()


def test_run_ablation_records_error_when_a_cell_crashes(tmp_path, monkeypatch):
    """One failing cell must not sink the whole grid; the entry is marked
    with the error and the harness moves on."""
    root = _make_tree(tmp_path)
    # Two targets, one config. Give the LLM only enough responses for the
    # first target; the second's T1 call raises.
    llm = FakeLLM(responses={"codegen": ["def test_a(): assert True\n"]})
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (_report(1, 0), _ok()))
    # score_seeded_bugs runs a real pytest; stub in the benchmark module too.
    monkeypatch.setattr(benchmark_mod, "run_pytest", lambda *_a, **_kw: _ok())

    report = run_ablation(
        targets=discover_targets(root),
        workdir_root=tmp_path / "grid",
        cfg=AppConfig(),
        llm=llm,
        configs=[AblationConfig("T1-only", max_rounds=1)],
    )

    assert len(report.entries) == 2
    errors = [e for e in report.entries if e.error is not None]
    ok = [e for e in report.entries if e.error is None]
    assert len(errors) == 1
    assert len(ok) == 1
    # The successful entry still has a kill rate.
    assert ok[0].kill_rate == 1.0
