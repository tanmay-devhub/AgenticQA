"""Sandbox backends.

Two implementations behind one interface (``run`` -> ``RunResult``):

    subprocess (default): run in-process on the host Python. Fast, Windows-
        friendly, no daemon. Safe on hand-picked benchmark code; NOT safe
        for arbitrary user code.
    docker:               run inside ``mutagen-sandbox:latest`` with the
        workdir mounted at /work, no network, unprivileged user. Required
        before pointing mutagen at anything you didn't write yourself.

Backend selection is per call via ``backend="subprocess"|"docker"``. Higher
layers (loop, MCP server) forward ``cfg.sandbox.backend`` down.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Backend = Literal["subprocess", "docker"]


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_s: int,
    env_overrides: dict[str, str] | None = None,
) -> RunResult:
    """Host-Python subprocess. See ``run`` for the shared contract."""
    import os
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    if env_overrides:
        env.update(env_overrides)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return RunResult(
            returncode=-1,
            stdout=e.stdout or "",
            stderr=(e.stderr or "") + f"\n[timeout after {timeout_s}s]",
            timed_out=True,
        )
    return RunResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        timed_out=False,
    )


def run(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_s: int,
    env_overrides: dict[str, str] | None = None,
    backend: Backend = "subprocess",
) -> RunResult:
    """Dispatch a run to the selected backend.

    Both backends promise:
      - captures stdout / stderr as UTF-8 strings (with replacement on
        invalid bytes),
      - returns ``timed_out=True`` if the wall-clock budget was blown,
      - never raises for a non-zero exit code (surface via ``returncode``).
    """
    if backend == "docker":
        # Local import so the subprocess path never has to touch docker code.
        from mutagen.sandbox.docker import run_docker
        return run_docker(cmd, cwd=cwd, timeout_s=timeout_s, env_overrides=env_overrides)
    return _run_subprocess(cmd, cwd=cwd, timeout_s=timeout_s, env_overrides=env_overrides)


def _pytest_argv(python_exe: str, coverage_source: str | None, extra_args: list[str] | None) -> list[str]:
    cmd = [python_exe, "-m", "pytest", "-q"]
    if coverage_source:
        cmd += [
            f"--cov={coverage_source}",
            "--cov-report=json:coverage.json",
            "--cov-report=",
        ]
    if extra_args:
        cmd.extend(extra_args)
    return cmd


def run_pytest(
    cwd: Path,
    *,
    timeout_s: int,
    extra_args: list[str] | None = None,
    coverage_source: str | None = None,
    backend: Backend = "subprocess",
) -> RunResult:
    """Run pytest inside ``cwd``.

    Subprocess backend uses the host ``sys.executable`` so the venv's pytest
    resolves; docker backend uses plain ``python`` on the container's PATH.
    Coverage is best-effort in both: if pytest-cov isn't available the outer
    caller should fall through gracefully.
    """
    python_exe = sys.executable if backend == "subprocess" else "python"
    cmd = _pytest_argv(python_exe, coverage_source, extra_args)
    return run(cmd, cwd=cwd, timeout_s=timeout_s, backend=backend)
