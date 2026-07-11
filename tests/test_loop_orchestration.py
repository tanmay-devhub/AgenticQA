"""Orchestration tests for `run_loop`.

Uses the real workdir + real filesystem, but stubs the expensive external calls
(pytest, mutmut) via monkeypatching. Keeps tests fast and hermetic while still
exercising the round-counting / stop-condition / plateau logic that is the
easiest place for regressions to slip in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mutagen.agent import loop as loop_mod
from mutagen.agent.testing import FakeLLM
from mutagen.config import AppConfig
from mutagen.mutation.report import Mutant, MutationReport
from mutagen.sandbox.executor import RunResult


TARGET_SRC = '''\
def add(a, b):
    return a + b
'''


def _write_target(tmp_path: Path) -> Path:
    p = tmp_path / "add.py"
    p.write_text(TARGET_SRC, encoding="utf-8")
    return p


def _ok(text: str = "") -> RunResult:
    return RunResult(returncode=0, stdout=text, stderr="", timed_out=False)


def _fail(text: str = "boom") -> RunResult:
    return RunResult(returncode=1, stdout=text, stderr="err", timed_out=False)


def _report(*, killed: int, survived: int, survivor_diffs: list[str] | None = None) -> MutationReport:
    survivor_diffs = survivor_diffs or []
    survivors = [
        Mutant(
            id=f"add.add__mutmut_{i}",
            file="target.py",
            line=2,
            status="survived",
            diff=d,
            kind="arithmetic",
        )
        for i, d in enumerate(survivor_diffs, 1)
    ]
    return MutationReport(
        total=killed + survived,
        killed=killed,
        survived=survived,
        survivors=survivors,
    )


def test_loop_stops_when_pytest_fails_in_round_1(tmp_path, monkeypatch):
    target = _write_target(tmp_path)
    # Codegen calls: initial tests + up to MAX_REPAIR_ATTEMPTS repair attempts.
    llm = FakeLLM(
        responses={"codegen": ["def test_x(): assert True\n"] * 3}
    )
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _fail("collection error"))
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (_report(killed=0, survived=0), _ok()))

    result = loop_mod.run_loop(
        target=target, workdir=tmp_path / "wd", cfg=AppConfig(), llm=llm, max_rounds=3
    )

    assert len(result.rounds) == 1
    assert result.rounds[0].pytest_ok is False
    assert result.rounds[0].repaired is True
    assert result.rounds[0].report is None
    assert "pytest failed in round 1" in result.stopped_reason


def test_loop_repair_rescues_round_when_second_pytest_passes(tmp_path, monkeypatch):
    """First repair fixes it: only one repair response consumed."""
    target = _write_target(tmp_path)
    llm = FakeLLM(responses={"codegen": ["def test_broken(: pass\n", "def test_x(): assert True\n"]})
    pytest_results = iter([_fail("SyntaxError"), _ok(), _ok()])
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: next(pytest_results))
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (_report(killed=5, survived=0), _ok()))

    result = loop_mod.run_loop(
        target=target, workdir=tmp_path / "wd", cfg=AppConfig(), llm=llm, max_rounds=3
    )

    assert len(result.rounds) == 1
    assert result.rounds[0].pytest_ok is True
    assert result.rounds[0].repaired is True
    assert result.rounds[0].report.kill_rate == 1.0


def test_loop_repair_rescues_on_second_attempt(tmp_path, monkeypatch):
    """First repair attempt still fails pytest; second attempt succeeds. This
    is the case that motivated the two-shot repair path -- the model sometimes
    needs a temperature bump to break out of a locked-in wrong expectation."""
    target = _write_target(tmp_path)
    llm = FakeLLM(
        responses={
            "codegen": [
                "def test_broken(: pass\n",         # T1 output (broken)
                "def test_still_broken(: pass\n",   # first repair (still broken)
                "def test_x(): assert True\n",      # second repair (fixed)
            ]
        }
    )
    pytest_results = iter([_fail("SyntaxError"), _fail("SyntaxError"), _ok(), _ok()])
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: next(pytest_results))
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (_report(killed=5, survived=0), _ok()))

    result = loop_mod.run_loop(
        target=target, workdir=tmp_path / "wd", cfg=AppConfig(), llm=llm, max_rounds=3
    )

    assert len(result.rounds) == 1
    assert result.rounds[0].pytest_ok is True
    assert result.rounds[0].repaired is True
    # Codegen was called 3 times: T1 + 2 repairs.
    assert sum(1 for c in llm.calls if c.role == "codegen") == 3


def test_loop_stops_when_no_survivors_after_round_1(tmp_path, monkeypatch):
    target = _write_target(tmp_path)
    llm = FakeLLM(responses={"codegen": ["def test_x(): assert True\n"]})
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (_report(killed=5, survived=0), _ok()))

    result = loop_mod.run_loop(
        target=target, workdir=tmp_path / "wd", cfg=AppConfig(), llm=llm, max_rounds=3
    )

    assert len(result.rounds) == 1
    assert result.stopped_reason == "no survivors after round 1"
    assert result.final_report is not None
    assert result.final_report.kill_rate == 1.0


def test_loop_stops_when_max_rounds_is_one(tmp_path, monkeypatch):
    target = _write_target(tmp_path)
    llm = FakeLLM(responses={"codegen": ["def test_x(): assert True\n"]})
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(
        loop_mod, "run_mutmut", lambda **_kw: (_report(killed=3, survived=2, survivor_diffs=["diff1", "diff2"]), _ok())
    )

    result = loop_mod.run_loop(
        target=target, workdir=tmp_path / "wd", cfg=AppConfig(), llm=llm, max_rounds=1
    )

    assert len(result.rounds) == 1
    assert "max_rounds" in result.stopped_reason


def test_loop_advances_to_round_2_when_survivors_exist(tmp_path, monkeypatch):
    target = _write_target(tmp_path)
    llm = FakeLLM(
        responses={
            "codegen": [
                "def test_r1(): assert True\n",
                "def test_r2(): assert True\n",
            ],
            "planner": ['{"verdict":"real_gap","reason":"needs boundary"}'],
        }
    )
    reports = iter([
        _report(killed=3, survived=1, survivor_diffs=["--- a/target.py\n+++ b/target.py\n@@ -2,1 +2,1 @@\n-    return a + b\n+    return a - b\n"]),
        _report(killed=4, survived=0),
    ])
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (next(reports), _ok()))

    result = loop_mod.run_loop(
        target=target, workdir=tmp_path / "wd", cfg=AppConfig(), llm=llm, max_rounds=3
    )

    assert len(result.rounds) == 2
    assert result.rounds[0].tier == 1
    assert result.rounds[1].tier == 2
    assert result.rounds[1].report.kill_rate == 1.0
    # planner should have been asked exactly once (1 unique survivor diff).
    planner_calls = [c for c in llm.calls if c.role == "planner"]
    assert len(planner_calls) == 1
    codegen_calls = [c for c in llm.calls if c.role == "codegen"]
    assert len(codegen_calls) == 2


def test_loop_escalates_to_t3_on_plateau_then_stops(tmp_path, monkeypatch):
    target = _write_target(tmp_path)
    diff = "--- a/target.py\n+++ b/target.py\n@@ -2,1 +2,1 @@\n-    return a + b\n+    return a * b\n"
    llm = FakeLLM(
        responses={
            "codegen": [
                "def test_r1(): assert True\n",  # T1
                "def test_r2(): assert True\n",  # T2
                "def test_r3(): assert True\n",  # T3 (escalation)
            ],
            "planner": [
                '{"verdict":"real_gap","reason":"still needs boundary"}',
                '{"verdict":"real_gap","reason":"still needs boundary"}',
            ],
        }
    )
    # Round 1: 0.75. Round 2 (T2): 0.76 -> plateau -> escalate to T3.
    # Round 3 (T3): 0.77 -> still plateau -> stop.
    reports = iter([
        _report(killed=3, survived=1, survivor_diffs=[diff]),
        _report(killed=76, survived=24, survivor_diffs=[diff + " "]),
        _report(killed=77, survived=23, survivor_diffs=[diff + "  "]),
    ])
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(loop_mod, "run_mutmut", lambda **_kw: (next(reports), _ok()))

    result = loop_mod.run_loop(
        target=target, workdir=tmp_path / "wd", cfg=AppConfig(), llm=llm, max_rounds=5
    )

    assert [r.tier for r in result.rounds] == [1, 2, 3]
    assert "plateau after T3" in result.stopped_reason


def test_loop_stops_when_planner_finds_no_real_gap(tmp_path, monkeypatch):
    target = _write_target(tmp_path)
    diff = "--- a/target.py\n+++ b/target.py\n@@ -2,1 +2,1 @@\n-    return a + b\n+    return a + b\n"
    llm = FakeLLM(
        responses={
            "codegen": ["def test_r1(): assert True\n"],
            "planner": ['{"verdict":"equivalent","reason":"no observable diff"}'],
        }
    )
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(
        loop_mod, "run_mutmut", lambda **_kw: (_report(killed=3, survived=1, survivor_diffs=[diff]), _ok())
    )

    result = loop_mod.run_loop(
        target=target, workdir=tmp_path / "wd", cfg=AppConfig(), llm=llm, max_rounds=3
    )

    assert len(result.rounds) == 1
    assert "no real_gap survivors" in result.stopped_reason
