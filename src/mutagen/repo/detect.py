"""Language detection by filename extension.

Not a full linguist-style classifier. For our purposes -- deciding which
mutation pipeline(s) to run against a cloned repo -- an extension table is
efficient, deterministic, and easy to override when a language grows.

Skips directories that never contain source we'd want to test (dependency
folders, VCS metadata, build outputs). The skip list stays wide because a
false negative (missing a Python file inside `node_modules` -- there
shouldn't be one) is cheaper than a false positive (counting 30k JS files
in `node_modules` as user code).
"""

from __future__ import annotations

from pathlib import Path

# One extension may map to only one language. If a mapping is ambiguous
# (``.h`` for both C and C++), we prefer the more common target usage.
LANG_BY_EXT: dict[str, str] = {
    ".py":   "python",
    ".pyi":  "python",
    ".js":   "javascript",
    ".mjs":  "javascript",
    ".cjs":  "javascript",
    ".jsx":  "javascript",
    ".ts":   "typescript",
    ".tsx":  "typescript",
    ".java": "java",
    ".cs":   "csharp",
    ".cpp":  "cpp",
    ".cc":   "cpp",
    ".cxx":  "cpp",
    ".hpp":  "cpp",
    ".hh":   "cpp",
    ".hxx":  "cpp",
    # ``.c`` and ``.h`` are deliberately absent for now -- they'd need
    # explicit C-vs-C++ disambiguation once we ship a C++ plugin.
}

# Directories skipped during the walk. Anything a package manager, VCS,
# or build system typically owns.
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", ".pnpm-store", ".yarn",
    "__pycache__", ".venv", "venv", "env",
    "build", "dist", "target", "bin", "obj", "out",
    ".next", ".nuxt", ".turbo", ".svelte-kit",
    ".gradle", ".idea", ".vscode",
    "coverage", "htmlcov", ".nyc_output",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".hypothesis",
    ".stryker-tmp", "StrykerOutput", "pit-reports", "mull-report",
    "CMakeFiles", "cmake-build-debug", "cmake-build-release",
})


def detect_languages(repo_path: Path) -> dict[str, int]:
    """Return ``{language: file_count}`` for source files under ``repo_path``.

    Languages with zero hits are omitted from the result. Ordering is by
    descending count so the caller can call ``next(iter(...))`` to get the
    dominant language.
    """
    repo_path = repo_path.resolve()
    if not repo_path.is_dir():
        return {}

    counts: dict[str, int] = {}
    for path in _walk_source_files(repo_path):
        lang = LANG_BY_EXT.get(path.suffix.lower())
        if lang is None:
            continue
        counts[lang] = counts.get(lang, 0) + 1

    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _walk_source_files(root: Path):
    """Yield candidate source files, pruning skip dirs at every level."""
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for child in children:
            if child.is_symlink():
                # Symlinks inside a cloned repo are rare and can loop. Skip.
                continue
            if child.is_dir():
                if child.name in _SKIP_DIRS or child.name.startswith("."):
                    continue
                stack.append(child)
            elif child.is_file():
                yield child
