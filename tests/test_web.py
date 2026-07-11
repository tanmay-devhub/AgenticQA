"""Route tests for the read-only dashboard.

FastAPI TestClient over a hand-built runs/ tree: no server, no browser, no LLM.
Covers happy path (real run.json + benchmark.json), degenerate paths (empty
folder, missing run.json, path traversal), and the raw-tests text endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mutagen.web import create_app


def _write_run(runs_root: Path, name: str, run_data: dict, target_src: str = "") -> Path:
    d = runs_root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps(run_data), encoding="utf-8")
    if target_src:
        (d / "target.py").write_text(target_src, encoding="utf-8")
    for i, r in enumerate(run_data.get("rounds", []), start=1):
        (d / f"round_{i}_report.json").write_text(json.dumps(r), encoding="utf-8")
        (d / f"test_round_{i}.py").write_text(
            f"# generated tests round {i}\ndef test_r{i}(): assert True\n",
            encoding="utf-8",
        )
    return d


def _sample_run(kill_rate: float = 0.9, rounds: int = 2) -> dict:
    return {
        "workdir": "runs/x",
        "stopped_reason": "max_rounds reached",
        "final_kill_rate": kill_rate,
        "total_usage": {
            "codegen": {"prompt_tokens": 500, "completion_tokens": 300, "calls": 2},
            "planner": {"prompt_tokens": 100, "completion_tokens": 50, "calls": 1},
        },
        "rounds": [
            {
                "index": i,
                "tier": 1 if i == 1 else 2,
                "tests_path": f"runs/x/test_round_{i}.py",
                "pytest_ok": True,
                "repaired": False,
                "elapsed_s": 5.0 * i,
                "report": {
                    "killed": 8 + i,
                    "survived": max(0, 2 - i),
                    "total": 10,
                    "kill_rate": kill_rate,
                    "survivors": [
                        {"id": f"s_{i}_1", "file": "target.py", "line": 3, "kind": "comparison",
                         "diff": "- return a\n+ return b\n"},
                    ] if i < rounds else [],
                },
                "coverage": {"missing_lines": [], "line_rate": 1.0},
                "usage": {
                    "codegen": {"prompt_tokens": 250, "completion_tokens": 150, "calls": 1},
                    "planner": {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
                },
            }
            for i in range(1, rounds + 1)
        ],
    }


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    _write_run(runs_root, "phase1-demo", _sample_run(kill_rate=0.966, rounds=1),
               target_src='"""Phase 1 target."""\ndef f(): return 1\n')
    _write_run(runs_root, "phase2-demo", _sample_run(kill_rate=0.935, rounds=3),
               target_src='"""Phase 2 target."""\ndef g(): return 2\n')
    # Write a benchmark folder too.
    bench_dir = runs_root / "bench-demo"
    bench_dir.mkdir()
    (bench_dir / "benchmark.json").write_text(json.dumps({
        "entries": [
            {
                "target": "benchmarks/x/target.py",
                "workdir": "runs/bench-demo/x_target",
                "wall_clock_s": 12.3,
                "error": None,
                "result": _sample_run(kill_rate=1.0, rounds=1),
                "seeded_bugs": [
                    {"bug_id": "bug_1", "description": "off-by-one", "caught": True},
                    {"bug_id": "bug_2", "description": "leap year", "caught": False},
                ],
                "seeded_bug_catch_rate": 0.5,
            },
        ],
        "mean_kill_rate": 1.0,
        "mean_seeded_bug_catch_rate": 0.5,
    }), encoding="utf-8")

    return TestClient(create_app(runs_root))


def test_landing_shows_new_run_form(client: TestClient) -> None:
    """GET / is the new-run form (landing swap)."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'name="target_source"' in r.text
    assert 'name="target_name"' in r.text


def test_new_alias_redirects_to_root(client: TestClient) -> None:
    r = client.get("/new", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/"


def test_runs_index_lists_runs_and_benches(client: TestClient) -> None:
    r = client.get("/runs")
    assert r.status_code == 200
    assert "phase1-demo" in r.text
    assert "phase2-demo" in r.text
    assert "bench-demo" in r.text
    # % rendering
    assert "96.6%" in r.text
    assert "93.5%" in r.text


def test_runs_index_empty_runs_root(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    c = TestClient(create_app(runs_root))
    r = c.get("/runs")
    assert r.status_code == 200
    assert "no runs found" in r.text


def test_run_detail_renders_chart_and_target(client: TestClient) -> None:
    r = client.get("/runs/phase2-demo")
    assert r.status_code == 200
    # target source is shown
    assert "Phase 2 target" in r.text
    # chart canvas is present
    assert "killChart" in r.text
    # rounds table has three rows
    assert r.text.count(">T1<") == 1
    assert r.text.count(">T2<") == 2


def test_run_detail_missing_run(client: TestClient) -> None:
    r = client.get("/runs/nope")
    assert r.status_code == 404


def test_run_detail_path_traversal_blocked(client: TestClient, tmp_path: Path) -> None:
    """The `_resolve_run` guard rejects any name whose resolved path escapes
    the runs root. httpx normalizes literal `..` client-side, so we test the
    guard directly rather than through a URL."""
    from mutagen.web.app import create_app  # noqa: F401
    # A URL-encoded `..` survives client normalization; FastAPI unquotes it.
    r = client.get("/runs/%2E%2E%2Fsecret")
    assert r.status_code in (400, 404)


def test_tests_endpoint_returns_generated_source(client: TestClient) -> None:
    r = client.get("/runs/phase2-demo/tests/2")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "generated tests round 2" in r.text


def test_tests_endpoint_missing_round(client: TestClient) -> None:
    r = client.get("/runs/phase2-demo/tests/99")
    assert r.status_code == 404


def test_api_run_returns_run_json(client: TestClient) -> None:
    r = client.get("/api/runs/phase1-demo")
    assert r.status_code == 200
    body = r.json()
    assert body["final_kill_rate"] == 0.966
    assert len(body["rounds"]) == 1


def test_bench_detail_renders_seeded_bugs(client: TestClient) -> None:
    r = client.get("/bench/bench-demo")
    assert r.status_code == 200
    assert "off-by-one" in r.text
    assert "leap year" in r.text
    # aggregate stats
    assert "100.0%" in r.text  # mean kill rate
    assert "50.0%" in r.text   # mean seeded catch


def test_bench_detail_missing(client: TestClient) -> None:
    r = client.get("/bench/nope")
    assert r.status_code == 404


def test_debrief_route_renders_markdown(client: TestClient, tmp_path: Path) -> None:
    """Point the client at a runs dir with a hand-written debrief and check
    the markdown lands in the rendered HTML."""
    runs_root = tmp_path / "debrief-runs"
    (runs_root / "sample").mkdir(parents=True)
    (runs_root / "sample" / "round_1_debrief.md").write_text(
        "# Round 1 — Tier 1\n\n## pytest\n\nInitial run: **passed** (no repair needed).\n\n"
        "## mutmut\n\nKilled **9 / 10** (kill rate 90.0%). Survived: **1**.\n\n"
        "### surviving mutants\n\n"
        "**`t.f__mutmut_1`** — kind=`comparison` (line 3)\n\n"
        "```diff\n- return a < b\n+ return a <= b\n```\n",
        encoding="utf-8",
    )
    c = TestClient(create_app(runs_root))

    r = c.get("/runs/sample/debrief/1")
    assert r.status_code == 200
    # Markdown headings turned into <h1>/<h2>/<h3>.
    assert "<h1>" in r.text and "Round 1" in r.text
    assert "<h2>" in r.text and "pytest" in r.text
    # Fenced code block preserved with language class.
    assert "language-diff" in r.text
    # Bold rendered.
    assert "<strong>passed</strong>" in r.text
    # Link back to raw markdown surface.
    assert "/runs/sample/debrief/1/raw" in r.text


def test_debrief_raw_returns_markdown(client: TestClient, tmp_path: Path) -> None:
    runs_root = tmp_path / "debrief-runs"
    (runs_root / "sample").mkdir(parents=True)
    src = "# hi\n\n- one\n- two\n"
    (runs_root / "sample" / "round_1_debrief.md").write_text(src, encoding="utf-8")
    c = TestClient(create_app(runs_root))

    r = c.get("/runs/sample/debrief/1/raw")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == src


def test_debrief_missing_returns_404(client: TestClient) -> None:
    r = client.get("/runs/phase1-demo/debrief/99")
    assert r.status_code == 404
