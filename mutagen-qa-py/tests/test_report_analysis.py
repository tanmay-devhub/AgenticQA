"""Unit tests for the post-run LLM analysis module.

We drive ``analyze_run`` with a ``FakeLLM`` so the tests are hermetic. The
report structure round-trip (dict <-> dataclass) and the graceful-degradation
paths (malformed JSON, LLM error, empty survivors) are the interesting
surface -- LLM prompt wording is not asserted on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mutagen.agent.testing import FakeLLM
from mutagen.mutation.report import Mutant, MutationReport
from mutagen.report import AnalysisReport, SurvivorAnalysis, analyze_run
from mutagen.report.analysis import (
    _coerce_enum,
    _extract_json,
    _VALID_CATEGORIES,
    _VALID_SEVERITIES,
)


def _survivor_json(**overrides) -> str:
    """Canned LLM response for one survivor -- fields match the analysis schema."""
    payload = {
        "root_cause": "no test asserts on empty-list input",
        "category": "test_gap",
        "severity": "high",
        "suggested_test": "def test_empty_list():\n    assert search([]) == -1\n",
        "fix_hint": "add a boundary test for empty input",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _verdict_json() -> str:
    return json.dumps({
        "verdict": "Coverage is strong on happy-path inputs but misses boundary cases.",
        "action_items": ["add empty-input tests", "test off-by-one edges"],
    })


def _make_workdir(tmp_path: Path) -> Path:
    """Give the analysis a target.py to find -- otherwise the prompt is empty."""
    wd = tmp_path / "run"
    wd.mkdir()
    (wd / "target.py").write_text("def search(xs, q):\n    return -1\n", encoding="utf-8")
    (wd / "test_round_1.py").write_text(
        "def test_dummy():\n    assert True\n", encoding="utf-8",
    )
    return wd


def _make_report(n_survivors: int = 1) -> MutationReport:
    survivors = [
        Mutant(
            id=f"mut_{i}", file="target.py", line=1 + i, status="survived",
            diff=f"@@ -1 +1 @@\n-original {i}\n+mutated {i}\n", kind="comparison",
        )
        for i in range(n_survivors)
    ]
    return MutationReport(
        total=10, killed=9, survived=n_survivors, timeout=0, suspicious=0, skipped=0,
        survivors=survivors,
    )


# --- pure helpers ---------------------------------------------------------


def test_extract_json_strips_markdown_fence():
    got = _extract_json("```json\n{\"a\": 1}\n```")
    assert got == {"a": 1}


def test_extract_json_finds_object_amid_prose():
    got = _extract_json("Sure! Here's the analysis: {\"category\": \"test_gap\"} done.")
    assert got == {"category": "test_gap"}


def test_extract_json_raises_when_no_object():
    with pytest.raises(ValueError):
        _extract_json("no braces here")


def test_coerce_enum_lowercases_and_validates():
    assert _coerce_enum("CRITICAL", _VALID_SEVERITIES, default="unknown") == "critical"
    assert _coerce_enum("bogus", _VALID_SEVERITIES, default="unknown") == "unknown"
    assert _coerce_enum(None, _VALID_CATEGORIES, default="unknown") == "unknown"


# --- AnalysisReport round trip -------------------------------------------


def test_analysis_report_round_trip_preserves_all_fields():
    original = AnalysisReport(
        workdir_name="run-abc", target_name="foo", generated_at=1234.5,
        model="fake/analysis", total_mutants=10, killed=9, survived=1,
        timeout=0, kill_rate=0.9,
        survivors=[SurvivorAnalysis(
            mutant_id="mut_0", file="target.py", line=2, kind="comparison",
            diff="- x\n+ y", root_cause="reason", category="test_gap",
            severity="high", suggested_test="assert 1", fix_hint="fix it",
        )],
        verdict="ok verdict",
        action_items=["do a", "do b"],
    )
    got = AnalysisReport.from_dict(original.to_dict())
    assert got.to_dict() == original.to_dict()


def test_severity_counts_include_zero_buckets():
    r = AnalysisReport(
        workdir_name="w", target_name="t", generated_at=0.0, model="m",
        total_mutants=0, killed=0, survived=0, timeout=0, kill_rate=0.0,
        survivors=[
            SurvivorAnalysis(
                mutant_id="1", file=None, line=None, kind="other", diff=None,
                root_cause="", category="unknown", severity="critical",
                suggested_test="", fix_hint="",
            ),
            SurvivorAnalysis(
                mutant_id="2", file=None, line=None, kind="other", diff=None,
                root_cause="", category="unknown", severity="critical",
                suggested_test="", fix_hint="",
            ),
        ],
    )
    counts = r.severity_counts()
    assert counts["critical"] == 2
    assert counts["high"] == 0
    assert counts["low"] == 0
    assert counts["unknown"] == 0


def test_sorted_survivors_puts_critical_first():
    entries = [
        SurvivorAnalysis(
            mutant_id="low", file=None, line=None, kind="other", diff=None,
            root_cause="", category="unknown", severity="low",
            suggested_test="", fix_hint="",
        ),
        SurvivorAnalysis(
            mutant_id="crit", file=None, line=None, kind="other", diff=None,
            root_cause="", category="unknown", severity="critical",
            suggested_test="", fix_hint="",
        ),
        SurvivorAnalysis(
            mutant_id="hi", file=None, line=None, kind="other", diff=None,
            root_cause="", category="unknown", severity="high",
            suggested_test="", fix_hint="",
        ),
    ]
    r = AnalysisReport(
        workdir_name="w", target_name="t", generated_at=0.0, model="m",
        total_mutants=0, killed=0, survived=0, timeout=0, kill_rate=0.0,
        survivors=entries,
    )
    order = [s.mutant_id for s in r.sorted_survivors()]
    assert order == ["crit", "hi", "low"]


# --- analyze_run end-to-end with FakeLLM ---------------------------------


def test_analyze_run_with_one_survivor(tmp_path: Path):
    wd = _make_workdir(tmp_path)
    llm = FakeLLM(responses={"analysis": [_survivor_json(), _verdict_json()]})
    got = analyze_run(
        wd, llm=llm, report=_make_report(1),
        target_name="my_target", model_name="fake/analysis",
    )
    assert got.target_name == "my_target"
    assert got.total_mutants == 10
    assert got.killed == 9
    assert len(got.survivors) == 1
    s = got.survivors[0]
    assert s.category == "test_gap"
    assert s.severity == "high"
    assert "empty" in s.root_cause
    assert got.verdict.startswith("Coverage")
    assert got.action_items == ["add empty-input tests", "test off-by-one edges"]


def test_analyze_run_no_survivors_skips_llm_verdict(tmp_path: Path):
    """Empty survivor list means kill-rate 100%; no LLM verdict call needed."""
    wd = _make_workdir(tmp_path)
    llm = FakeLLM(responses={})  # No canned responses -- must NOT be called.
    got = analyze_run(
        wd, llm=llm, report=_make_report(0),
        target_name="perfect", model_name="fake/analysis",
    )
    assert got.survivors == []
    assert got.action_items == []
    assert "killed" in got.verdict.lower() or "covers" in got.verdict.lower()
    assert llm.calls == []


def test_analyze_run_degrades_on_malformed_llm_json(tmp_path: Path):
    """A survivor whose LLM response is unparseable gets a fallback analysis
    with severity=unknown -- the rest of the report still renders."""
    wd = _make_workdir(tmp_path)
    llm = FakeLLM(responses={"analysis": ["not JSON at all", _verdict_json()]})
    got = analyze_run(
        wd, llm=llm, report=_make_report(1),
        target_name="t", model_name="fake/analysis",
    )
    assert len(got.survivors) == 1
    assert got.survivors[0].severity == "unknown"
    assert got.survivors[0].category == "unknown"
    assert "analysis unavailable" in got.survivors[0].root_cause


def test_analyze_run_multiple_survivors_each_get_own_call(tmp_path: Path):
    wd = _make_workdir(tmp_path)
    llm = FakeLLM(responses={"analysis": [
        _survivor_json(severity="critical", category="test_gap"),
        _survivor_json(severity="low", category="equivalent"),
        _survivor_json(severity="high", category="spec_gap"),
        _verdict_json(),
    ]})
    got = analyze_run(
        wd, llm=llm, report=_make_report(3),
        target_name="t", model_name="fake/analysis",
    )
    assert [s.severity for s in got.survivors] == ["critical", "low", "high"]
    # 3 survivor calls + 1 verdict call = 4 analysis-role invocations.
    assert sum(1 for c in llm.calls if c.role == "analysis") == 4
