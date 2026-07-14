"""Read-only web dashboard for browsing mutagen runs.

The dashboard is a thin read layer over the on-disk artifacts the loop already
writes (``run.json``, per-round reports, generated tests, coverage) -- it never
mutates state, so it's safe to run against an actively-writing runs folder.
"""

from mutagen.web.app import create_app

__all__ = ["create_app"]
