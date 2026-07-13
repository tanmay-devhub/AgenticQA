"""Shallow git clone helper.

Contract:
    - HTTPS git URLs and ``file://`` URLs are accepted. SSH / git:// are
      rejected -- SSH would need agent forwarding in server deployments,
      and unauthenticated git:// is deprecated.
    - Depth is 1 by default. Test-generation doesn't need history; the
      clone is a snapshot.
    - Timeout is enforced. A slow / hanging remote can't wedge a job.
    - Destination must not already exist as a non-empty directory.

Failures raise ``CloneError`` so the caller can surface a clean message to
the SSE stream instead of a raw traceback.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

# Loose but useful: catches obvious garbage (``javascript:``, ``sh://``,
# spaces, no scheme at all) without banning legitimate mirrors. The
# scheme allowlist is the safety net; regex is the smoke test.
_SCHEME_ALLOW = ("https://", "http://", "file://")
_URL_SANITY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^\s]+$")


class CloneError(RuntimeError):
    """Any failure surfaced to the caller: bad URL, git not on PATH, timeout,
    non-empty destination, or a non-zero git exit code."""


def _validate_url(url: str) -> None:
    if not isinstance(url, str) or not url.strip():
        raise CloneError("repo URL is empty")
    if not _URL_SANITY_RE.match(url):
        raise CloneError(f"repo URL is not a valid URL: {url!r}")
    if not url.startswith(_SCHEME_ALLOW):
        raise CloneError(
            f"repo URL must use https:// (or file:// for local testing); got {url!r}"
        )


def _validate_dest(dest: Path) -> None:
    if dest.exists():
        if not dest.is_dir():
            raise CloneError(f"destination exists and is not a directory: {dest}")
        # ``.git`` presence would mean we're overlapping an existing clone --
        # git would refuse anyway, but this gives a clearer message.
        if any(dest.iterdir()):
            raise CloneError(f"destination is not empty: {dest}")


def clone_repo(
    url: str,
    dest: Path,
    *,
    depth: int = 1,
    timeout_s: int = 120,
) -> Path:
    """Shallow-clone ``url`` into ``dest``. Returns the destination path.

    ``depth=1`` is a deliberate default: we generate tests from source, not
    from history. If you ever need history for a specific analysis, override
    at the call site rather than here.
    """
    _validate_url(url)
    dest = dest.resolve()
    _validate_dest(dest)
    dest.mkdir(parents=True, exist_ok=True)

    git = shutil.which("git")
    if not git:
        raise CloneError("git CLI not found on PATH")

    cmd = [git, "clone", "--depth", str(depth), "--single-branch", url, str(dest)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired as e:
        # Leave a partial clone visible for debugging but don't retry --
        # a slow remote at N seconds is likely slow at 2N seconds.
        raise CloneError(f"git clone timed out after {timeout_s}s") from e

    if proc.returncode != 0:
        # git writes progress to stderr; truncate so the error message
        # stays readable in the SSE stream / UI.
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise CloneError(f"git clone failed (exit {proc.returncode}): {tail}")
    return dest
