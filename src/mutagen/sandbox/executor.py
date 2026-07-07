"""Sandbox backends.

Subprocess backend for now (Windows-friendly, no daemon). Runs a command
with a timeout and captures stdout/stderr. Docker backend later, same shape.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def run(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_s: int,
    env_overrides: dict[str, str] | None = None,
) -> RunResult:
    """Run a command in ``cwd`` with a wall-clock timeout, capturing streams.

    ``env_overrides`` merges into the current environment (never replaces it),
    so subprocesses still see PATH etc. We force UTF-8 I/O by default because
    several tools (notably mutmut) crash on Windows cp1252 when printing emoji.
    """
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


def run_pytest(cwd: Path, *, timeout_s: int, extra_args: list[str] | None = None) -> RunResult:
    """Run pytest inside ``cwd`` using the current Python interpreter."""
    cmd = [sys.executable, "-m", "pytest", "-q"]
    if extra_args:
        cmd.extend(extra_args)
    return run(cmd, cwd=cwd, timeout_s=timeout_s)
