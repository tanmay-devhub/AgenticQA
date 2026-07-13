"""Tests for the optional plain-English testing focus feature.

Covers three layers:
  1. Web submit -- ``focus`` form field lands on the Job and on disk.
  2. Codegen prompts -- when ``focus.txt`` exists in the workdir, the tier1/2/3
     prompts include the focus directive; when absent, prompts are unchanged.
  3. Report analysis -- ``analyze_run`` reads ``focus.txt`` and passes it into
     the per-survivor + verdict prompts, and echoes it into ``AnalysisReport``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mutagen.agent.testing import FakeLLM
from mutagen.mutation.report import Mutant, MutationReport
from mutagen.report import analyze_run
from mutagen.testgen import tier1, tier2, tier3
from mutagen.web import create_app
from mutagen.web.jobs import JobRegistry


# --- web layer -----------------------------------------------------------


def _tiny_target() -> str:
    return "def parse_amount(s: str) -> int:\n    return int(s)\n"


def _make_client(tmp_path: Path) -> tuple[TestClient, Path]:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    reg = JobRegistry(runs_root, llm_factory=lambda: FakeLLM())
    app = create_app(runs_root, jobs=reg)
    return TestClient(app), runs_root


def test_submit_persists_focus_to_workdir(tmp_path: Path):
    client, runs_root = _make_client(tmp_path)
    focus_text = "test negative numbers and non-numeric strings for parse_amount"
    r = client.post("/jobs", data={
        "target_name": "amt", "max_rounds": "1",
        "target_source": _tiny_target(), "focus": focus_text,
    })
    assert r.status_code in (200, 303)  # redirect on browser Accept

    workdirs = list(runs_root.iterdir())
    assert len(workdirs) == 1
    focus_file = workdirs[0] / "focus.txt"
    assert focus_file.is_file()
    assert focus_file.read_text(encoding="utf-8") == focus_text


def test_submit_without_focus_does_not_create_focus_file(tmp_path: Path):
    client, runs_root = _make_client(tmp_path)
    r = client.post("/jobs", data={
        "target_name": "amt", "max_rounds": "1", "target_source": _tiny_target(),
    })
    assert r.status_code in (200, 303)
    workdirs = list(runs_root.iterdir())
    assert not (workdirs[0] / "focus.txt").is_file()


def test_submit_focus_whitespace_only_treated_as_absent(tmp_path: Path):
    """Empty textarea posts an empty string; we should not persist that as
    a real focus (would just noise up the prompts)."""
    client, runs_root = _make_client(tmp_path)
    r = client.post("/jobs", data={
        "target_name": "amt", "max_rounds": "1",
        "target_source": _tiny_target(), "focus": "   \n\n  ",
    })
    assert r.status_code in (200, 303)
    workdirs = list(runs_root.iterdir())
    assert not (workdirs[0] / "focus.txt").is_file()


# --- codegen prompts ----------------------------------------------------


def _make_workdir_with_target(tmp_path: Path, *, focus: str | None) -> Path:
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "target.py").write_text(_tiny_target(), encoding="utf-8")
    if focus is not None:
        (wd / "focus.txt").write_text(focus, encoding="utf-8")
    return wd


def test_tier1_prompt_includes_focus_directive_when_set(tmp_path: Path):
    wd = _make_workdir_with_target(tmp_path, focus="cover unicode strings")
    fake = FakeLLM(responses={"codegen": ["def test_x(): assert True\n"]})
    tier1.generate(fake, target_source=wd / "target.py")
    assert len(fake.calls) == 1
    user_msg = fake.calls[0].user
    assert "TESTING FOCUS" in user_msg
    assert "cover unicode strings" in user_msg


def test_tier1_prompt_omits_focus_directive_when_absent(tmp_path: Path):
    wd = _make_workdir_with_target(tmp_path, focus=None)
    fake = FakeLLM(responses={"codegen": ["def test_x(): assert True\n"]})
    tier1.generate(fake, target_source=wd / "target.py")
    user_msg = fake.calls[0].user
    assert "TESTING FOCUS" not in user_msg


def test_tier2_prompt_carries_focus(tmp_path: Path):
    from mutagen.agent.planner import TestSpec
    wd = _make_workdir_with_target(tmp_path, focus="error paths only")
    fake = FakeLLM(responses={"codegen": ["def test_x(): assert True\n"]})
    spec = TestSpec(
        file="target.py", function="parse_amount", survivors=[],
        dominant_kind="comparison", technique_hint="boundary tests",
        uncovered_lines=[],
    )
    tier2.generate(fake, target_source=wd / "target.py", specs=[spec])
    assert "error paths only" in fake.calls[0].user


def test_tier3_prompt_carries_focus(tmp_path: Path):
    from mutagen.agent.planner import TestSpec
    wd = _make_workdir_with_target(tmp_path, focus="round-trip invariants")
    fake = FakeLLM(responses={"codegen": ["def test_x(): assert True\n"]})
    spec = TestSpec(
        file="target.py", function="parse_amount", survivors=[],
        dominant_kind="arithmetic", technique_hint="Hypothesis",
        uncovered_lines=[],
    )
    tier3.generate(fake, target_source=wd / "target.py", specs=[spec])
    assert "round-trip invariants" in fake.calls[0].user


# --- report analysis ----------------------------------------------------


def _survivor_json() -> str:
    return json.dumps({
        "root_cause": "no test asserts on the focused input",
        "category": "test_gap",
        "severity": "critical",
        "suggested_test": "def test_focus(): assert True",
        "fix_hint": "add a test in the focus area",
    })


def _verdict_json() -> str:
    return json.dumps({
        "verdict": "Focus area has one critical gap.",
        "action_items": ["patch the focus area first"],
    })


def _make_report(n: int = 1) -> MutationReport:
    survivors = [
        Mutant(
            id=f"mut_{i}", file="target.py", line=2, status="survived",
            diff="- x\n+ y", kind="comparison",
        ) for i in range(n)
    ]
    return MutationReport(
        total=10, killed=10 - n, survived=n,
        timeout=0, suspicious=0, skipped=0, survivors=survivors,
    )


def test_analyze_run_reads_focus_from_workdir_and_uses_in_prompts(tmp_path: Path):
    wd = _make_workdir_with_target(tmp_path, focus="test negative amounts specifically")
    (wd / "test_round_1.py").write_text("def test_x(): pass", encoding="utf-8")
    fake = FakeLLM(responses={"analysis": [_survivor_json(), _verdict_json()]})
    got = analyze_run(
        wd, llm=fake, report=_make_report(1),
        target_name="parse_amount", model_name="fake",
    )
    # focus is echoed on the report itself.
    assert got.focus == "test negative amounts specifically"
    # And injected into BOTH the survivor and verdict prompts.
    assert len(fake.calls) == 2
    for call in fake.calls:
        assert "USER TESTING FOCUS" in call.user
        assert "test negative amounts specifically" in call.user


def test_analyze_run_no_focus_omits_directive(tmp_path: Path):
    wd = _make_workdir_with_target(tmp_path, focus=None)
    (wd / "test_round_1.py").write_text("def test_x(): pass", encoding="utf-8")
    fake = FakeLLM(responses={"analysis": [_survivor_json(), _verdict_json()]})
    got = analyze_run(
        wd, llm=fake, report=_make_report(1),
        target_name="parse_amount", model_name="fake",
    )
    assert got.focus is None
    for call in fake.calls:
        assert "USER TESTING FOCUS" not in call.user


def test_analysis_report_round_trip_preserves_focus():
    from mutagen.report import AnalysisReport
    original = AnalysisReport(
        workdir_name="w", target_name="t", generated_at=1.0, model="m",
        total_mutants=0, killed=0, survived=0, timeout=0, kill_rate=0.0,
        focus="my focus",
    )
    got = AnalysisReport.from_dict(original.to_dict())
    assert got.focus == "my focus"
