"""Route tests for the LLM-driven mutation report views.

FastAPI TestClient with a FakeLLM factory so we can trigger the "generate"
background task in-process without hitting a real model.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mutagen.agent.testing import FakeLLM
from mutagen.web import create_app


# --- helpers -------------------------------------------------------------


def _write_run(runs_root: Path, name: str, *, with_survivors: bool = True) -> Path:
    """Emit a minimally realistic run tree that the report code can analyze."""
    d = runs_root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "target.py").write_text(
        "def search(xs, q):\n    return -1\n", encoding="utf-8",
    )
    (d / "test_round_1.py").write_text(
        "def test_dummy():\n    assert True\n", encoding="utf-8",
    )
    survivors = [{
        "id": "mut_0", "file": "target.py", "line": 2, "kind": "comparison",
        "status": "survived", "diff": "- return -1\n+ return 0\n",
    }] if with_survivors else []
    run_data = {
        "workdir": f"runs/{name}",
        "stopped_reason": "max_rounds",
        "final_kill_rate": 0.9,
        "total_usage": {
            "codegen": {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
            "planner": {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
        },
        "rounds": [{
            "index": 1, "tier": 1, "tests_path": f"runs/{name}/test_round_1.py",
            "pytest_ok": True, "repaired": False, "elapsed_s": 1.0,
            "report": {
                "killed": 9, "survived": len(survivors), "total": 10,
                "timeout": 0, "suspicious": 0, "skipped": 0,
                "kill_rate": 0.9, "survivors": survivors, "disabled_types": [],
            },
            "usage": {
                "codegen": {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
                "planner": {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
            },
        }],
    }
    (d / "run.json").write_text(json.dumps(run_data), encoding="utf-8")
    (d / "round_1_report.json").write_text(
        json.dumps(run_data["rounds"][0]), encoding="utf-8",
    )
    return d


def _survivor_json_text() -> str:
    return json.dumps({
        "root_cause": "no test asserts on empty-list input",
        "category": "test_gap",
        "severity": "high",
        "suggested_test": "def test_empty(): assert search([], 1) == -1",
        "fix_hint": "add empty-list boundary test",
    })


def _verdict_json_text() -> str:
    return json.dumps({
        "verdict": "Good coverage, one boundary gap remains.",
        "action_items": ["add empty-list test"],
    })


@pytest.fixture
def client_factory(tmp_path: Path):
    """Build a TestClient with a FakeLLM stocked for one full analysis run."""
    def make(*, canned: list[str] | None = None) -> tuple[TestClient, Path]:
        runs_root = tmp_path / "runs"
        runs_root.mkdir()
        fake = FakeLLM(responses={
            "analysis": canned if canned is not None
            else [_survivor_json_text(), _verdict_json_text()]
        })
        app = create_app(runs_root, llm_factory=lambda: fake)
        return TestClient(app), runs_root
    return make


# --- report view page ----------------------------------------------------


def test_report_page_shows_hint_when_run_not_finished(client_factory):
    client, runs_root = client_factory()
    (runs_root / "unfinished").mkdir()  # no run.json
    (runs_root / "unfinished" / "target.py").write_text("x=1", encoding="utf-8")
    r = client.get("/runs/unfinished/report")
    assert r.status_code == 200
    assert "hasn't finished" in r.text or "hasn" in r.text


def test_report_page_shows_generate_button_when_run_complete_but_no_analysis(client_factory):
    client, runs_root = client_factory()
    _write_run(runs_root, "phase1")
    r = client.get("/runs/phase1/report")
    assert r.status_code == 200
    assert "generate report" in r.text


def test_report_page_renders_analysis_when_present(client_factory):
    """If analysis.json already exists we render the full report page,
    including severity pills + verdict."""
    client, runs_root = client_factory()
    d = _write_run(runs_root, "phase1")
    (d / "analysis.json").write_text(json.dumps({
        "workdir_name": "phase1", "target_name": "phase1", "generated_at": 1.0,
        "model": "fake", "total_mutants": 10, "killed": 9, "survived": 1,
        "timeout": 0, "kill_rate": 0.9,
        "survivors": [{
            "mutant_id": "mut_0", "file": "target.py", "line": 2,
            "kind": "comparison", "diff": "- a\n+ b",
            "root_cause": "test misses empty-list", "category": "test_gap",
            "severity": "critical", "suggested_test": "x=1", "fix_hint": "fix",
        }],
        "verdict": "Overall solid.", "action_items": ["do a"],
    }), encoding="utf-8")

    r = client.get("/runs/phase1/report")
    assert r.status_code == 200
    body = r.text
    assert "critical" in body
    assert "test_gap" in body
    assert "test misses empty-list" in body
    assert "Overall solid." in body


# --- generation endpoint --------------------------------------------------


def test_generate_returns_409_when_run_incomplete(client_factory):
    client, runs_root = client_factory()
    (runs_root / "unfinished").mkdir()
    r = client.post("/api/runs/unfinished/report/generate")
    assert r.status_code == 409


def test_generate_kicks_off_and_produces_analysis_json(client_factory):
    """POST returns 202; FastAPI's TestClient runs BackgroundTasks before
    returning, so ``analysis.json`` is on disk by the time we check."""
    client, runs_root = client_factory()
    d = _write_run(runs_root, "phase1")
    r = client.post("/api/runs/phase1/report/generate")
    assert r.status_code == 202
    assert (d / "analysis.json").is_file()
    got = json.loads((d / "analysis.json").read_text(encoding="utf-8"))
    assert got["survived"] == 1
    assert got["survivors"][0]["severity"] == "high"
    # Pending marker should be cleared by the finally block.
    assert not (d / "analysis.pending").is_file()


def test_generate_short_circuits_when_analysis_already_exists(client_factory):
    """A second generate call must not re-invoke the LLM (would raise 'no more
    canned responses'). It should just return 200 with state=ready."""
    client, runs_root = client_factory()
    d = _write_run(runs_root, "phase1")
    r = client.post("/api/runs/phase1/report/generate")
    assert r.status_code == 202
    assert (d / "analysis.json").is_file()
    r2 = client.post("/api/runs/phase1/report/generate")
    assert r2.status_code == 200
    assert r2.json()["state"] == "ready"


def test_status_reports_ready_after_generation(client_factory):
    client, runs_root = client_factory()
    _write_run(runs_root, "phase1")
    client.post("/api/runs/phase1/report/generate")
    r = client.get("/api/runs/phase1/report/status")
    assert r.status_code == 200
    assert r.json() == {"state": "ready"}


def test_status_missing_before_any_generation(client_factory):
    client, runs_root = client_factory()
    _write_run(runs_root, "phase1")
    r = client.get("/api/runs/phase1/report/status")
    assert r.status_code == 200
    assert r.json() == {"state": "missing"}


# --- PDF endpoint --------------------------------------------------------


def test_pdf_endpoint_404_without_analysis(client_factory):
    client, runs_root = client_factory()
    _write_run(runs_root, "phase1")
    r = client.get("/runs/phase1/report.pdf")
    assert r.status_code == 404


def test_pdf_endpoint_returns_pdf_bytes_after_generation(client_factory):
    """The PDF stream must start with the PDF magic and carry the download
    header so browsers save-as instead of trying to render inline."""
    client, runs_root = client_factory()
    _write_run(runs_root, "phase1")
    client.post("/api/runs/phase1/report/generate")
    r = client.get("/runs/phase1/report.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert 'attachment; filename="phase1_report.pdf"' in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF-")
    assert len(r.content) > 500  # sanity: real PDF, not a stub
