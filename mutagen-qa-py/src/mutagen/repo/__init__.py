"""Repository ingestion: clone a git URL, detect languages, discover targets.

Kept language-agnostic. The Python loop consumes what this package produces;
future JS/TS/Java/C#/C++ pipelines will hook into the same detection layer.
"""

from mutagen.repo.clone import CloneError, clone_repo
from mutagen.repo.detect import LANG_BY_EXT, detect_languages

__all__ = ["CloneError", "clone_repo", "LANG_BY_EXT", "detect_languages"]
