"""Tests for the repo-ingestion package.

We exercise clone against a genuine local git repo (created in tmp), not a
mocked subprocess -- git's argv is stable enough that the extra realism is
worth the ~50ms it costs. Language detection is pure filesystem-walk, so
no clone is needed for it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from mutagen.repo import clone_repo, detect_languages
from mutagen.repo.clone import CloneError


def _has_git() -> bool:
    return shutil.which("git") is not None


needs_git = pytest.mark.skipif(not _has_git(), reason="git CLI not on PATH")


# --- URL validation --------------------------------------------------------


def test_clone_rejects_empty_url(tmp_path: Path) -> None:
    with pytest.raises(CloneError, match="empty"):
        clone_repo("", tmp_path / "x")


def test_clone_rejects_ssh_scheme(tmp_path: Path) -> None:
    """SSH would need agent forwarding on the server; deliberately unsupported.

    SCP-style (``git@host:path``) fails the URL-shape regex before the scheme
    check runs, so we just assert it doesn't get through; either message is
    a sound rejection."""
    with pytest.raises(CloneError):
        clone_repo("git@github.com:foo/bar.git", tmp_path / "x")
    with pytest.raises(CloneError, match="https://"):
        clone_repo("ssh://git@github.com/foo/bar.git", tmp_path / "x")


def test_clone_rejects_garbage_url(tmp_path: Path) -> None:
    with pytest.raises(CloneError):
        clone_repo("not a url at all", tmp_path / "x")


def test_clone_rejects_non_empty_destination(tmp_path: Path) -> None:
    dest = tmp_path / "already-here"
    dest.mkdir()
    (dest / "leftover.txt").write_text("x", encoding="utf-8")
    with pytest.raises(CloneError, match="not empty"):
        clone_repo("https://example.com/foo.git", dest)


# --- real git roundtrip ----------------------------------------------------


def _init_local_repo(root: Path, files: dict[str, str]) -> Path:
    """Create a bare-ish local repo with a single commit containing ``files``.
    Returns a path suitable for use as a ``file://`` clone source."""
    src = root / "origin"
    src.mkdir()
    for rel, body in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    def run(*args):
        subprocess.run(["git", *args], cwd=src, check=True,
                       env={**env, **__import__("os").environ},
                       capture_output=True)

    run("init", "-b", "main")
    run("add", ".")
    run("commit", "-m", "init")
    return src


@needs_git
def test_clone_repo_roundtrip(tmp_path: Path) -> None:
    """file:// clone into an empty dir yields the same files."""
    origin = _init_local_repo(tmp_path, {"README.md": "hi\n", "src/x.py": "x = 1\n"})
    dest = tmp_path / "cloned"
    got = clone_repo(f"file://{origin.as_posix()}", dest, timeout_s=60)
    assert got == dest.resolve()
    assert (dest / "README.md").read_text(encoding="utf-8") == "hi\n"
    assert (dest / "src" / "x.py").read_text(encoding="utf-8") == "x = 1\n"
    # Shallow clone -> exactly one entry in the reflog / log.
    log = subprocess.run(["git", "-C", str(dest), "log", "--oneline"],
                         capture_output=True, text=True, check=True)
    assert len(log.stdout.strip().splitlines()) == 1


# --- language detection ----------------------------------------------------


def _touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("", encoding="utf-8")


def test_detect_languages_counts_by_extension(tmp_path: Path) -> None:
    _touch(tmp_path, "a.py")
    _touch(tmp_path, "sub/b.py")
    _touch(tmp_path, "web/app.ts")
    _touch(tmp_path, "web/lib/util.tsx")
    _touch(tmp_path, "web/util.js")
    _touch(tmp_path, "svc/Main.java")
    _touch(tmp_path, "svc/App.cs")
    _touch(tmp_path, "native/x.cpp")
    _touch(tmp_path, "README.md")   # not a source language we handle

    langs = detect_languages(tmp_path)
    assert langs == {
        "python": 2,
        "typescript": 2,
        "javascript": 1,
        "java": 1,
        "csharp": 1,
        "cpp": 1,
    }
    # Sorted by descending count (python + typescript tie at 2 -> alphabetical).
    assert list(langs.keys())[0] in ("python", "typescript")


def test_detect_languages_skips_noise_dirs(tmp_path: Path) -> None:
    """node_modules / __pycache__ / .git are not user code."""
    _touch(tmp_path, "app.py")
    _touch(tmp_path, "node_modules/pkg/index.js")
    _touch(tmp_path, "__pycache__/cache.py")
    _touch(tmp_path, ".git/HEAD")
    _touch(tmp_path, "build/dist/x.js")
    _touch(tmp_path, "target/classes/X.class")

    langs = detect_languages(tmp_path)
    # Only the real app.py should count.
    assert langs == {"python": 1}


def test_detect_languages_empty_dir(tmp_path: Path) -> None:
    assert detect_languages(tmp_path) == {}


def test_detect_languages_missing_dir(tmp_path: Path) -> None:
    assert detect_languages(tmp_path / "nope") == {}
