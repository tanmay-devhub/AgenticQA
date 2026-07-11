"""Verify the loop drops round_N_report.json + run.json into the workdir."""

from __future__ import annotations

import json
from pathlib import Path

from mutagen.agent import loop as loop_mod
from mutagen.agent.testing import FakeLLM
from mutagen.config import AppConfig
from mutagen.mutation.report import Mutant, MutationReport
from mutagen.sandbox.executor import RunResult


TARGET_SRC = "def add(a, b):\n    return a + b\n"


def _ok() -> RunResult:
    return RunResult(returncode=0, stdout="", stderr="", timed_out=False)


def _report() -> MutationReport:
    return MutationReport(
        total=1,
        killed=1,
        survived=0,
        survivors=[],
    )


def test_persist_round_and_run(tmp_path: Path, monkeypatch):
    target = tmp_path / "add.py"
    target.write_text(TARGET_SRC, encoding="utf-8")
    llm = FakeLLM(responses={"codegen": ["def test_x(): assert True\n"]})
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (_report(), _ok()))

    workdir = tmp_path / "wd"
    loop_mod.run_loop(target=target, workdir=workdir, cfg=AppConfig(), llm=llm, max_rounds=1)

    round_path = workdir / "round_1_report.json"
    run_path = workdir / "run.json"
    assert round_path.exists()
    assert run_path.exists()

    round_json = json.loads(round_path.read_text(encoding="utf-8"))
    assert round_json["index"] == 1
    assert round_json["tier"] == 1
    assert round_json["report"]["killed"] == 1

    run_json = json.loads(run_path.read_text(encoding="utf-8"))
    assert run_json["final_kill_rate"] == 1.0
    assert len(run_json["rounds"]) == 1


def test_persist_run_even_when_pytest_fails(tmp_path: Path, monkeypatch):
    target = tmp_path / "add.py"
    target.write_text(TARGET_SRC, encoding="utf-8")
    # T1 + up to MAX_REPAIR_ATTEMPTS repair shots.
    llm = FakeLLM(responses={"codegen": ["def test_x(): assert True\n"] * 3})
    monkeypatch.setattr(
        loop_mod, "run_pytest",
        lambda *_a, **_kw: RunResult(returncode=1, stdout="", stderr="err", timed_out=False),
    )
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (_report(), _ok()))

    workdir = tmp_path / "wd"
    loop_mod.run_loop(target=target, workdir=workdir, cfg=AppConfig(), llm=llm, max_rounds=1)

    assert (workdir / "run.json").exists()
    payload = json.loads((workdir / "run.json").read_text(encoding="utf-8"))
    assert "pytest failed" in payload["stopped_reason"]
