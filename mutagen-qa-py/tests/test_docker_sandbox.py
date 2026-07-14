"""Docker sandbox backend tests.

We do NOT actually run Docker in unit tests -- that would make CI depend on
a live daemon. Instead we stub ``subprocess.run`` and verify:

    - The docker CLI argv is well-formed (image, mount, workdir, network,
      env pass-through).
    - Image-existence check gates a rebuild.
    - Missing docker surfaces DockerNotAvailable, not a silent fallback.
    - ``run_pytest`` with ``backend='docker'`` routes through this module
      and swaps ``sys.executable`` for the container-side ``python``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mutagen.sandbox import docker as docker_mod
from mutagen.sandbox import executor as executor_mod
from mutagen.sandbox.docker import DockerNotAvailable, run_docker
from mutagen.sandbox.executor import run_pytest


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_docker_builds_expected_argv(tmp_path, monkeypatch):
    """``run_docker`` must mount cwd, disable networking, and use the sandbox image."""
    captured: dict = {}

    def fake_which(name):
        assert name == "docker"
        return "/fake/docker"

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return _FakeCompleted(returncode=0, stdout="ok", stderr="")

    # Pretend the image is already present so we don't try to build.
    monkeypatch.setattr(docker_mod, "_image_exists", lambda _d: True)
    monkeypatch.setattr(docker_mod.shutil, "which", fake_which)
    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)

    result = run_docker(
        ["python", "-c", "print('hi')"],
        cwd=tmp_path,
        timeout_s=10,
        env_overrides={"FOO": "bar"},
    )

    assert result.returncode == 0
    assert result.stdout == "ok"

    cmd = captured["cmd"]
    assert cmd[0] == "/fake/docker"
    assert cmd[1:3] == ["run", "--rm"]
    # Mount uses forward-slash absolute path (Docker Desktop friendly).
    assert "-v" in cmd
    v_idx = cmd.index("-v")
    # Windows drive letters ("C:") mean the mount spec has more than one ':'.
    # rsplit(':', 1) leaves the /work suffix intact regardless of platform.
    src, dst = cmd[v_idx + 1].rsplit(":", 1)
    assert dst == "/work"
    assert src.endswith(tmp_path.name)  # tmp_path resolved & POSIX
    assert "-w" in cmd and cmd[cmd.index("-w") + 1] == "/work"
    assert "--network=none" in cmd
    # env pass-through
    assert "-e" in cmd
    e_idx = cmd.index("-e")
    assert cmd[e_idx + 1] == "FOO=bar"
    # image + user command trail the docker args
    assert docker_mod.IMAGE_TAG in cmd
    tail = cmd[cmd.index(docker_mod.IMAGE_TAG) + 1:]
    assert tail == ["python", "-c", "print('hi')"]


def test_run_docker_builds_image_on_first_use(tmp_path, monkeypatch):
    """If the image isn't present, ``ensure_image`` builds before running."""
    events: list[str] = []

    def fake_image_exists(_d):
        events.append("check")
        # First call misses; after "build" we won't call again in this test.
        return False

    def fake_build(_d):
        events.append("build")

    def fake_run(cmd, **_kw):
        events.append("run")
        return _FakeCompleted(returncode=0)

    monkeypatch.setattr(docker_mod, "_image_exists", fake_image_exists)
    monkeypatch.setattr(docker_mod, "_build_image", fake_build)
    monkeypatch.setattr(docker_mod.shutil, "which", lambda _n: "/fake/docker")
    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)

    run_docker(["python", "-V"], cwd=tmp_path, timeout_s=5)

    # build precedes the actual command execution.
    assert events == ["check", "build", "run"]


def test_run_docker_raises_when_docker_missing(tmp_path, monkeypatch):
    """No docker on PATH -> we surface it, not silently fall through."""
    monkeypatch.setattr(docker_mod.shutil, "which", lambda _n: None)

    with pytest.raises(DockerNotAvailable) as excinfo:
        run_docker(["python", "-V"], cwd=tmp_path, timeout_s=5)
    assert "docker CLI" in str(excinfo.value)


def test_run_docker_timeout_returns_timed_out(tmp_path, monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"], output="", stderr="")

    monkeypatch.setattr(docker_mod, "_image_exists", lambda _d: True)
    monkeypatch.setattr(docker_mod.shutil, "which", lambda _n: "/fake/docker")
    monkeypatch.setattr(docker_mod.subprocess, "run", fake_run)

    result = run_docker(["python", "-V"], cwd=tmp_path, timeout_s=1)
    assert result.timed_out is True
    assert result.returncode == -1
    assert "docker timeout" in result.stderr


def test_run_pytest_routes_docker_backend(tmp_path, monkeypatch):
    """``run_pytest(backend='docker')`` invokes ``run_docker`` with a
    container-side python (never the host's ``sys.executable``)."""
    captured: dict = {}

    def fake_run_docker(cmd, *, cwd, timeout_s, env_overrides=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout_s"] = timeout_s
        from mutagen.sandbox.executor import RunResult
        return RunResult(returncode=0, stdout="ok", stderr="", timed_out=False)

    monkeypatch.setattr(docker_mod, "run_docker", fake_run_docker)

    result = run_pytest(tmp_path, timeout_s=15, backend="docker", coverage_source="target")
    assert result.returncode == 0
    # First token is the container python, NOT the host absolute python path.
    assert captured["cmd"][0] == "python"
    assert "-m" in captured["cmd"] and "pytest" in captured["cmd"]
    assert "--cov=target" in captured["cmd"]


def test_config_env_var_flips_backend_to_docker(monkeypatch):
    """MUTAGEN_SANDBOX_BACKEND=docker sets AppConfig().sandbox.backend."""
    from mutagen.config import AppConfig
    monkeypatch.setenv("MUTAGEN_SANDBOX_BACKEND", "docker")
    assert AppConfig().sandbox.backend == "docker"


def test_config_default_backend_is_subprocess(monkeypatch):
    monkeypatch.delenv("MUTAGEN_SANDBOX_BACKEND", raising=False)
    from mutagen.config import AppConfig
    assert AppConfig().sandbox.backend == "subprocess"
