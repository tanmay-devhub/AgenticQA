"""Tests for the per-round Markdown debrief writer.

Covers the parts the human reader most needs to trust:
    - Passed-first-time rounds don't get a bogus "failed" narrative.
    - Failed-then-repaired rounds show BOTH sides (which tests failed
      originally + the final pass).
    - Surviving mutants are listed with their diffs so the reader can
      form an opinion without opening the artifacts.
    - The handoff section is appended (not rewriting the body) and
      names the technique the next round will use.
"""

from __future__ import annotations

from pathlib import Path

from mutagen.agent.classifier import ClassifiedSurvivor
from mutagen.agent.debrief import (
    _parse_failing_tests,
    append_handoff,
    write_round_body,
)
from mutagen.agent.loop import RoundResult
from mutagen.agent.planner import TestSpec
from mutagen.mutation.report import Mutant, MutationReport
from mutagen.sandbox.executor import RunResult


def _rr(returncode: int, stdout: str = "", stderr: str = "") -> RunResult:
    return RunResult(returncode=returncode, stdout=stdout, stderr=stderr, timed_out=False)


def _survivor(idx: int, kind: str = "comparison", diff: str = "") -> Mutant:
    return Mutant(
        id=f"target.f__mutmut_{idx}",
        file="target.py", line=3 + idx, status="survived",
        diff=diff or f"-    return a < b\n+    return a <= b\n",
        kind=kind,
    )


def _round(index: int, *, tier: int = 1, pytest_ok: bool = True,
           report: MutationReport | None = None,
           repaired: bool = False,
           initial: RunResult | None = None,
           final_result: RunResult | None = None) -> RoundResult:
    return RoundResult(
        index=index,
        tier=tier,
        tests_path=Path(f"test_round_{index}.py"),
        pytest_result=final_result or _rr(0),
        pytest_ok=pytest_ok,
        report=report,
        elapsed_s=5.0,
        repaired=repaired,
        initial_pytest_result=initial,
    )


def test_parse_failing_tests_extracts_names_and_reasons() -> None:
    out = _parse_failing_tests(
        "some noise\n"
        "FAILED tests/test_x.py::test_a - AssertionError: assert 1 == 2\n"
        "FAILED tests/test_x.py::test_b\n"
        "== summary ==\n"
    )
    assert out == [
        ("tests/test_x.py::test_a", "AssertionError: assert 1 == 2"),
        ("tests/test_x.py::test_b", ""),
    ]


def test_write_round_body_passed_first_time(tmp_path: Path) -> None:
    r = _round(1, report=MutationReport(total=10, killed=10, survived=0, survivors=[]))
    p = write_round_body(tmp_path, r)
    text = p.read_text(encoding="utf-8")
    assert "Round 1 — Tier 1" in text
    assert "Initial run: **passed**" in text
    assert "No repair attempted." in text
    assert "Killed **10 / 10**" in text
    # No surviving-mutants section when there are none.
    assert "surviving mutants" not in text


def test_write_round_body_failed_then_repaired(tmp_path: Path) -> None:
    """The debrief must record BOTH sides: what the LLM first shipped that
    broke, and the final passing state."""
    initial = _rr(1, stdout=(
        "== short test summary info ==\n"
        "FAILED tests/test_x.py::test_edge - AssertionError: -1 != 1\n"
        "FAILED tests/test_x.py::test_none - TypeError: got None\n"
    ))
    r = _round(
        2, tier=2, pytest_ok=True, repaired=True, initial=initial,
        report=MutationReport(total=8, killed=7, survived=1,
                              survivors=[_survivor(1, "comparison")]),
    )
    text = write_round_body(tmp_path, r).read_text(encoding="utf-8")

    assert "Initial run: **FAILED**" in text
    assert "test_edge" in text and "test_none" in text
    assert "AssertionError: -1 != 1" in text
    assert "Repair was invoked" in text
    assert "Final pytest run: **passed**" in text
    # Survivor is listed with its diff.
    assert "target.f__mutmut_1" in text
    assert "```diff" in text
    assert "return a <= b" in text


def test_append_handoff_records_verdicts_and_specs(tmp_path: Path) -> None:
    r = _round(1, report=MutationReport(
        total=5, killed=3, survived=2,
        survivors=[_survivor(1, "comparison"), _survivor(2, "arithmetic")],
    ))
    write_round_body(tmp_path, r)

    classified = [
        ClassifiedSurvivor(_survivor(1, "comparison"), "real_gap", "boundary"),
        ClassifiedSurvivor(_survivor(2, "arithmetic"), "equivalent", "same output"),
    ]
    spec = TestSpec(
        file="target.py", function="f",
        dominant_kind="comparison",
        technique_hint="boundary tests around the operands",
        survivors=[_survivor(1, "comparison")],
        uncovered_lines=[7, 8],
    )
    append_handoff(
        tmp_path, 1, next_round_index=2, next_tier=2,
        classified=classified, specs=[spec],
    )
    text = (tmp_path / "round_1_debrief.md").read_text(encoding="utf-8")

    # Body from write_round_body is still there.
    assert "Round 1 — Tier 1" in text
    # Handoff was appended.
    assert "handoff to round 2" in text
    assert "Next tier: **T2**" in text
    assert "`real_gap`: **1**" in text
    assert "`equivalent`: **1**" in text
    assert "boundary tests around the operands" in text
    assert "uncovered lines in span: [7, 8]" in text


def test_append_handoff_when_no_specs(tmp_path: Path) -> None:
    """No real_gap survivors -> the loop stops, and the handoff section
    should explain that rather than silently omit itself."""
    r = _round(1, report=MutationReport(total=1, killed=0, survived=1,
                                        survivors=[_survivor(1)]))
    write_round_body(tmp_path, r)
    append_handoff(
        tmp_path, 1, next_round_index=2, next_tier=2,
        classified=[ClassifiedSurvivor(_survivor(1), "equivalent", "same output")],
        specs=[],
    )
    text = (tmp_path / "round_1_debrief.md").read_text(encoding="utf-8")
    assert "no real_gap survivors" in text.lower() or "no specs" in text.lower()
    assert "The loop will stop here" in text


def test_write_round_body_skips_mutmut_when_pytest_still_failed(tmp_path: Path) -> None:
    """If repair couldn't rescue the round, mutmut never ran -- the debrief
    must reflect that instead of pretending kill_rate=0."""
    initial = _rr(1, stdout="FAILED tests/test_x.py::test_a - SyntaxError\n")
    r = _round(
        1, pytest_ok=False, repaired=True, initial=initial,
        final_result=_rr(1, stdout="FAILED tests/test_x.py::test_a - SyntaxError\n"),
        report=None,
    )
    text = write_round_body(tmp_path, r).read_text(encoding="utf-8")
    assert "still failed" in text or "still failing" in text
    assert "Mutmut was **skipped**" in text
