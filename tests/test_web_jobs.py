"""Job submission / SSE / auth / health tests over the FastAPI app.

We inject a ``FakeLLM`` factory and stub the loop's expensive calls so a
"real" job completes in milliseconds. No real subprocess, no real LLM.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mutagen.agent import loop as loop_mod
from mutagen.agent.testing import FakeLLM
from mutagen.mutation.report import MutationReport
from mutagen.sandbox.executor import RunResult
from mutagen.web import create_app
from mutagen.web.jobs import JobRegistry


def _ok() -> RunResult:
    return RunResult(returncode=0, stdout="", stderr="", timed_out=False)


@pytest.fixture(autouse=True)
def _stub_loop_deps(monkeypatch):
    """Every job in this file gets a fast, deterministic loop."""
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(
        loop_mod, "run_mutmut",
        lambda **_kw: (MutationReport(total=5, killed=5, survived=0), _ok()),
    )


@pytest.fixture
def app_and_client(tmp_path: Path):
    runs = tmp_path / "runs"
    llm = FakeLLM(responses={"codegen": ["def test_a(): assert True\n"] * 30})
    registry = JobRegistry(runs, llm_factory=lambda: llm)
    app = create_app(runs, jobs=registry)
    return app, TestClient(app)


def _wait_for_status(client: TestClient, job_id: str, target: str, timeout_s: float = 5.0):
    """Poll until job hits a terminal status or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, r.text
        j = r.json()
        if j["status"] == target:
            return j
        if j["status"] in ("done", "failed", "cancelled") and j["status"] != target:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job did not reach {target!r} within {timeout_s}s")


def test_healthz_ok(app_and_client):
    _app, c = app_and_client
    r = c.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "runs_root" in body
    assert body["auth"] == "open"  # no MUTAGEN_WEB_AUTH_TOKEN set


def test_new_run_form_renders(app_and_client):
    _app, c = app_and_client
    r = c.get("/new")
    assert r.status_code == 200
    assert 'name="target_source"' in r.text
    assert 'name="target_name"' in r.text


def test_submit_and_complete_job_json(app_and_client):
    _app, c = app_and_client
    r = c.post(
        "/jobs",
        data={
            "target_source": '"""tiny."""\ndef f(x): return x\n',
            "target_name": "tiny",
            "max_rounds": "1",
        },
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["target_name"] == "tiny"
    assert job["max_rounds"] == 1

    final = _wait_for_status(c, job["id"], "done")
    assert final["status"] == "done"
    assert final["final_kill_rate"] == 1.0
    # Artifacts landed on disk under runs/.
    wd = Path(final["workdir"])
    assert (wd / "target.py").exists()
    assert (wd / "run.json").exists()


def test_submit_browser_gets_redirect(app_and_client):
    _app, c = app_and_client
    r = c.post(
        "/jobs",
        data={
            "target_source": "def g(): return 2\n",
            "target_name": "g",
            "max_rounds": "1",
        },
        # Browsers send Accept: text/html, ...
        headers={"Accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/jobs/")


def test_submit_rejects_empty_source(app_and_client):
    _app, c = app_and_client
    r = c.post(
        "/jobs",
        data={"target_source": "  \n", "target_name": "x", "max_rounds": "1"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()


def test_submit_rejects_out_of_range_rounds(app_and_client):
    _app, c = app_and_client
    r = c.post(
        "/jobs",
        data={"target_source": "def f(): pass\n", "target_name": "x", "max_rounds": "99"},
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 400


def test_jobs_list_html_shows_submitted(app_and_client):
    _app, c = app_and_client
    c.post("/jobs",
           data={"target_source": "def f(): return 1\n", "target_name": "list-me", "max_rounds": "1"},
           headers={"Accept": "application/json"})
    r = c.get("/jobs")
    assert r.status_code == 200
    assert "list-me" in r.text


def test_job_detail_page_renders(app_and_client):
    _app, c = app_and_client
    job = c.post("/jobs",
                 data={"target_source": "def f(): return 1\n", "target_name": "detail-me", "max_rounds": "1"},
                 headers={"Accept": "application/json"}).json()
    _wait_for_status(c, job["id"], "done")
    r = c.get(f"/jobs/{job['id']}")
    assert r.status_code == 200
    assert "detail-me" in r.text
    # SSE URL is wired into the JS via a template literal
    # (`/api/jobs/${jobId}/events`), so the ID appears separately.
    assert job["id"] in r.text
    assert "/api/jobs/${jobId}/events" in r.text


def test_api_jobs_lists_and_detail(app_and_client):
    _app, c = app_and_client
    j1 = c.post("/jobs",
                data={"target_source": "def a(): return 1\n", "target_name": "a", "max_rounds": "1"},
                headers={"Accept": "application/json"}).json()
    _wait_for_status(c, j1["id"], "done")
    r = c.get("/api/jobs")
    assert r.status_code == 200
    assert any(j["id"] == j1["id"] for j in r.json())
    r2 = c.get(f"/api/jobs/{j1['id']}")
    assert r2.status_code == 200
    assert r2.json()["target_name"] == "a"


def test_cancel_missing_job_404s(app_and_client):
    _app, c = app_and_client
    r = c.delete("/api/jobs/nonesuch")
    assert r.status_code == 404


def test_sse_stream_yields_snapshot_and_events(app_and_client):
    """One end-to-end SSE consumption. httpx TestClient supports SSE via
    iter_lines on a streaming response."""
    _app, c = app_and_client
    job = c.post("/jobs",
                 data={"target_source": "def f(): return 1\n", "target_name": "sse-me", "max_rounds": "1"},
                 headers={"Accept": "application/json"}).json()

    with c.stream("GET", f"/api/jobs/{job['id']}/events") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines: list[str] = []
        for raw in resp.iter_lines():
            if not raw:
                continue
            lines.append(raw)
            # Stop as soon as we've captured the terminal event.
            if raw == "event: end":
                break
    joined = "\n".join(lines)
    assert "event: snapshot" in joined
    assert '"type": "started"' in joined or "\"type\": \"started\"" in joined
    assert '"type": "done"' in joined
    assert "event: end" in joined


def test_auth_gated_when_token_set(app_and_client, monkeypatch):
    """POST /jobs and DELETE require Bearer <token> when MUTAGEN_WEB_AUTH_TOKEN
    is set. GETs stay open."""
    _app, c = app_and_client
    monkeypatch.setenv("MUTAGEN_WEB_AUTH_TOKEN", "s3cret")

    # Read is still open.
    assert c.get("/").status_code == 200
    assert c.get("/healthz").status_code == 200
    # /healthz shows auth is now configured.
    assert c.get("/healthz").json()["auth"] == "configured"

    # POST without header -> 401.
    r = c.post("/jobs",
               data={"target_source": "def f(): return 1\n", "target_name": "x", "max_rounds": "1"},
               headers={"Accept": "application/json"})
    assert r.status_code == 401

    # POST with wrong token -> 401.
    r = c.post("/jobs",
               data={"target_source": "def f(): return 1\n", "target_name": "x", "max_rounds": "1"},
               headers={"Accept": "application/json", "Authorization": "Bearer wrong"})
    assert r.status_code == 401

    # POST with correct token -> 201.
    r = c.post("/jobs",
               data={"target_source": "def f(): return 1\n", "target_name": "x", "max_rounds": "1"},
               headers={"Accept": "application/json", "Authorization": "Bearer s3cret"})
    assert r.status_code == 201, r.text


def test_static_has_cache_headers(app_and_client):
    _app, c = app_and_client
    r = c.get("/static/style.css")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "max-age" in cc
    assert "immutable" in cc


def test_favicon_served(app_and_client):
    _app, c = app_and_client
    r = c.get("/static/favicon.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg")


def test_topnav_visible_on_index(app_and_client):
    _app, c = app_and_client
    # / is now the new-run form itself; topnav links point at runs/jobs.
    r = c.get("/")
    assert 'href="/runs"' in r.text
    assert 'href="/jobs"' in r.text
    # And the form is on the landing page.
    assert 'name="target_source"' in r.text
