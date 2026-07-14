"""Docker sandbox backend.

Same public shape as ``sandbox.executor.run`` -- takes a command + cwd +
timeout, returns a ``RunResult`` -- but runs the command inside a
``mutagen-sandbox:latest`` container with the workdir mounted at ``/work``.
Used when ``cfg.sandbox.backend == "docker"`` (or ``MUTAGEN_SANDBOX_BACKEND=docker``).

Why: the subprocess backend runs generated tests + target code with the host
Python and its full filesystem. That's fine on a hand-picked benchmark; it is
not fine on arbitrary code. The Docker backend caps blast radius: unprivileged
user, ephemeral container, only ``cwd`` visible.

First use of any target auto-builds the image from ``docker/sandbox/Dockerfile``
if it's missing. If Docker itself is missing we surface a clear message
instead of silently falling back -- the whole point of picking this backend
is to isolate the run, not to run unsandboxed with a warning nobody reads.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from mutagen.sandbox.executor import RunResult

IMAGE_TAG = "mutagen-sandbox:latest"
_DOCKERFILE_DIR = Path(__file__).resolve().parents[3] / "docker" / "sandbox"


class DockerNotAvailable(RuntimeError):
    """Raised when the caller asked for docker but the daemon isn't reachable."""


def _which_docker() -> str:
    """Return the docker CLI path, raising DockerNotAvailable if missing."""
    p = shutil.which("docker")
    if not p:
        raise DockerNotAvailable("docker CLI not found on PATH")
    return p


def _image_exists(docker: str) -> bool:
    r = subprocess.run(
        [docker, "image", "inspect", IMAGE_TAG],
        capture_output=True, text=True, check=False,
    )
    return r.returncode == 0


def _build_image(docker: str) -> None:
    """Build the sandbox image from docker/sandbox/. Best-effort logging via
    stderr on the caller's terminal so users see progress on first use."""
    if not _DOCKERFILE_DIR.is_dir():
        raise DockerNotAvailable(
            f"Dockerfile directory {_DOCKERFILE_DIR} not found; "
            "reinstall mutagen or place a Dockerfile there manually"
        )
    r = subprocess.run(
        [docker, "build", "-t", IMAGE_TAG, str(_DOCKERFILE_DIR)],
        check=False,
    )
    if r.returncode != 0:
        raise DockerNotAvailable(
            f"docker build failed (exit {r.returncode}); see above output"
        )


def ensure_image() -> None:
    """Idempotent: build the sandbox image iff it isn't already present."""
    docker = _which_docker()
    if not _image_exists(docker):
        _build_image(docker)


def _windows_bind_path(cwd: Path) -> str:
    """Docker Desktop on Windows accepts POSIX-style paths for -v mounts."""
    return cwd.resolve().as_posix()


def run_docker(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_s: int,
    env_overrides: dict[str, str] | None = None,
) -> RunResult:
    """Run ``cmd`` inside the sandbox container, cwd mounted at /work.

    The subprocess backend expects host absolute paths (``sys.executable``);
    the docker equivalent expects PATH-resolved binaries (``python``, ``mutmut``).
    We do NOT rewrite the command here -- the caller (run_pytest, run_mutmut)
    is responsible for building a container-appropriate argv.
    """
    docker = _which_docker()
    ensure_image()

    bind_src = _windows_bind_path(cwd)
    docker_cmd = [
        docker, "run", "--rm",
        "-v", f"{bind_src}:/work",
        "-w", "/work",
        # Isolation defaults. Users who need networking (e.g. a target that
        # imports from a private registry) can drop --network=none via env.
        "--network=none",
    ]
    if env_overrides:
        for k, v in env_overrides.items():
            docker_cmd += ["-e", f"{k}={v}"]
    docker_cmd += [IMAGE_TAG, *cmd]

    try:
        proc = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s + 15,  # docker startup adds ~1-2s
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        return RunResult(
            returncode=-1,
            stdout=e.stdout or "",
            stderr=(e.stderr or "") + f"\n[docker timeout after {timeout_s}s]",
            timed_out=True,
        )
    return RunResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        timed_out=False,
    )
