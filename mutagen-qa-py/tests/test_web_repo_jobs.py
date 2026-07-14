"""End-to-end tests for repo-mode job submission.

A local ``file://`` origin stands in for a real GitHub URL: same code path
through ``git clone``, no network dependency.
"""

from __future__ import annotations

import os
import shutil
import subprocess
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


needs_git = pytest.mark.skipif(shutil.which("git") is None,
                               reason="git CLI not on PATH")


def _ok() -> RunResult:
    return RunResult(returncode=0, stdout="", stderr="", timed_out=False)


def _init_repo(root: Path, files: dict[str, str]) -> Path:
    src = root / "origin"
    src.mkdir()
    for rel, body in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           **os.environ}
    for args in (["init", "-b", "main"], ["add", "."], ["commit", "-m", "init"]):
        subprocess.run(["git", *args], cwd=src, check=True,
                       env=env, capture_output=True)
    return src


@pytest.fixture(autouse=True)
def _stub_loop_deps(monkeypatch):
    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(
        loop_mod, "run_mutmut",
        lambda **_kw: (MutationReport(total=4, killed=4, survived=0), _ok()),
    )


@pytest.fixture
def app_and_client(tmp_path: Path):
    runs = tmp_path / "runs"
    llm = FakeLLM(responses={"codegen": ["def test_a(): assert True\n"] * 20})
    registry = JobRegistry(runs, llm_factory=lambda: llm)
    app = create_app(runs, jobs=registry)
    return app, TestClient(app), tmp_path


def _wait_terminal(client: TestClient, job_id: str, timeout_s: float = 15.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job never terminated: {j}")


def test_submit_requires_exactly_one_source(app_and_client):
    _app, c, _ = app_and_client
    # Neither field.
    r = c.post("/jobs", data={"target_name": "x", "max_rounds": "1"},
               headers={"Accept": "application/json"})
    assert r.status_code == 400
    assert "exactly one" in r.json()["detail"].lower()
    # Both fields.
    r = c.post("/jobs", data={
        "target_name": "x", "max_rounds": "1",
        "target_source": "def f(): pass\n",
        "repo_url": "https://example.com/foo.git",
        "repo_target_path": "x.py",
    }, headers={"Accept": "application/json"})
    assert r.status_code == 400


def test_submit_repo_mode_requires_target_path(app_and_client):
    _app, c, _ = app_and_client
    r = c.post("/jobs", data={
        "target_name": "x", "max_rounds": "1",
        "repo_url": "https://example.com/foo.git",
    }, headers={"Accept": "application/json"})
    assert r.status_code == 400
    assert "repo_target_path" in r.json()["detail"]


@needs_git
def test_submit_repo_mode_end_to_end(app_and_client):
    """Clone -> language census -> run loop -> artifacts on disk."""
    _app, c, tmp_path = app_and_client
    origin = _init_repo(tmp_path, {
        "src/mod/utils.py": '"""tiny target."""\ndef inc(x: int) -> int:\n    return x + 1\n',
        "README.md": "hi\n",
    })

    r = c.post("/jobs", data={
        "target_name": "clone-test",
        "max_rounds": "1",
        "repo_url": f"file://{origin.as_posix()}",
        "repo_target_path": "src/mod/utils.py",
    }, headers={"Accept": "application/json"})
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    final = _wait_terminal(c, job_id)
    assert final["status"] == "done", final
    assert final["final_kill_rate"] == 1.0
    assert final["repo_url"].startswith("file://")
    assert final["repo_target_path"] == "src/mod/utils.py"
    # Language census recorded.
    assert final["languages"].get("python") == 1

    wd = Path(final["workdir"])
    assert (wd / "_repo").is_dir()               # clone landed
    assert (wd / "_input.py").is_file()          # sidecar copied
    assert (wd / "target.py").is_file()          # loop copied it
    assert (wd / "run.json").is_file()           # loop wrote its result


@needs_git
def test_repo_mode_rejects_path_traversal(app_and_client):
    _app, c, tmp_path = app_and_client
    origin = _init_repo(tmp_path, {"a.py": "x=1\n"})
    r = c.post("/jobs", data={
        "target_name": "traversal",
        "max_rounds": "1",
        "repo_url": f"file://{origin.as_posix()}",
        "repo_target_path": "../../../etc/passwd",
    }, headers={"Accept": "application/json"})
    # Submit accepts (path check happens inside runner) -> job status=failed.
    assert r.status_code == 201
    job_id = r.json()["id"]
    final = _wait_terminal(c, job_id)
    assert final["status"] == "failed"
    assert "escapes repo root" in (final["error"] or "") or \
           "not found" in (final["error"] or "")


@needs_git
def test_repo_mode_rejects_unsupported_target_extension(app_and_client, monkeypatch):
    # After the JS pipeline landed, .js targets are dispatched to
    # mutagen-qa-js. Verify the rejection contract still holds for extensions
    # we don't have a runner for yet (e.g. Java, C++).
    monkeypatch.setenv("MUTAGEN_JS_BIN", "/does-not-exist")  # keep JS path from spawning
    _app, c, tmp_path = app_and_client
    origin = _init_repo(tmp_path, {"src/Main.java": "class Main {}\n"})
    r = c.post("/jobs", data={
        "target_name": "java-target",
        "max_rounds": "1",
        "repo_url": f"file://{origin.as_posix()}",
        "repo_target_path": "src/Main.java",
    }, headers={"Accept": "application/json"})
    assert r.status_code == 201
    final = _wait_terminal(c, r.json()["id"])
    assert final["status"] == "failed"
    assert "unsupported target extension" in (final["error"] or "")


def test_repo_mode_infers_javascript_language_for_js_target(app_and_client, monkeypatch):
    # For .js targets the dispatcher should set language=javascript on the
    # job. Point MUTAGEN_JS_BIN at a stub so the dispatch fails fast without
    # requiring a working Node install in CI.
    monkeypatch.setenv("MUTAGEN_JS_BIN", "/does-not-exist")
    _app, c, tmp_path = app_and_client
    origin = _init_repo(tmp_path, {"web/index.js": "export const x = 1;\n"})
    r = c.post("/jobs", data={
        "target_name": "js-target",
        "max_rounds": "1",
        "repo_url": f"file://{origin.as_posix()}",
        "repo_target_path": "web/index.js",
    }, headers={"Accept": "application/json"})
    assert r.status_code == 201
    final = _wait_terminal(c, r.json()["id"])
    # Dispatch reaches the JS branch; since the bin doesn't exist, we surface
    # the resolver error. That's the useful assertion: language was inferred.
    assert final["language"] == "javascript"
    assert final["status"] == "failed"
    assert "mutagen-qa-js CLI not found" in (final["error"] or "")
