"""Opt-in bearer-token auth for write endpoints.

Contract:
    - If ``MUTAGEN_WEB_AUTH_TOKEN`` is unset, everything is open (local dev
      default). This is the same posture the CLI already assumes.
    - If it is set, ``require_write_auth`` (used on POST / DELETE) enforces
      ``Authorization: Bearer <token>`` with a constant-time comparison.
      Read endpoints (dashboard views) stay open so viewers don't need a
      token to browse results.

Kept minimal on purpose. If you need role-based access, OAuth, or
per-user quotas, this is not the layer -- put mutagen behind a proper
identity proxy (Cloudflare Access, oauth2-proxy, etc.) and let it inject
a trusted header instead.
"""

from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request, status

_ENV_VAR = "MUTAGEN_WEB_AUTH_TOKEN"


def _configured_token() -> str | None:
    """Read the token every call. This lets tests set/unset via monkeypatch
    without having to restart the app."""
    tok = os.environ.get(_ENV_VAR, "").strip()
    return tok or None


def is_auth_configured() -> bool:
    return _configured_token() is not None


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def require_write_auth(request: Request) -> None:
    """FastAPI dependency for POST / DELETE endpoints.

    No-op when auth is unconfigured (local dev). When configured, rejects
    requests without a matching bearer token. 401 (not 403) so browsers /
    curl know to attach credentials.
    """
    expected = _configured_token()
    if expected is None:
        return
    got = _extract_bearer(request)
    if got is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Constant-time to avoid timing side-channels on the token compare.
    if not hmac.compare_digest(got, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
