"""Provider-agnostic LLM client (litellm-backed).

Two roles from config:
    codegen  -- default: Ollama Cloud `qwen3-coder:480b-cloud`.
    planner  -- default: `gemini/gemini-2.5-pro`.

Provider swaps are one config edit. Zero-cost path = point `codegen` at a
local Ollama model, e.g. `ollama/qwen3-coder:30b`, without code changes.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Literal

import litellm

from mutagen.config import AppConfig, LLMRole

Role = Literal["codegen", "planner"]

# Retry only on errors that plausibly recover on retry. Auth / bad-request
# type errors are permanent -- retrying just burns time and money.
_RETRYABLE_LITELLM_ERROR_NAMES = frozenset({
    "RateLimitError",
    "Timeout",
    "APIConnectionError",
    "ServiceUnavailableError",
    "InternalServerError",
})


def _is_retryable(exc: BaseException) -> bool:
    """True iff ``exc`` looks like a transient network / rate-limit issue.

    Uses class name matching instead of ``isinstance`` because litellm
    re-exports exception classes at unstable paths across versions.
    """
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__
    if name in _RETRYABLE_LITELLM_ERROR_NAMES:
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "timeout" in msg or "connection" in msg


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    model: str


@dataclass
class RoleUsage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Usage:
    codegen: RoleUsage = field(default_factory=RoleUsage)
    planner: RoleUsage = field(default_factory=RoleUsage)

    def record(self, role: Role, resp: LLMResponse) -> None:
        bucket: RoleUsage = getattr(self, role)
        bucket.calls += 1
        bucket.prompt_tokens += resp.prompt_tokens or 0
        bucket.completion_tokens += resp.completion_tokens or 0

    def snapshot(self) -> Usage:
        return Usage(
            codegen=RoleUsage(
                calls=self.codegen.calls,
                prompt_tokens=self.codegen.prompt_tokens,
                completion_tokens=self.codegen.completion_tokens,
            ),
            planner=RoleUsage(
                calls=self.planner.calls,
                prompt_tokens=self.planner.prompt_tokens,
                completion_tokens=self.planner.completion_tokens,
            ),
        )

    def delta(self, previous: Usage) -> Usage:
        return Usage(
            codegen=RoleUsage(
                calls=self.codegen.calls - previous.codegen.calls,
                prompt_tokens=self.codegen.prompt_tokens - previous.codegen.prompt_tokens,
                completion_tokens=self.codegen.completion_tokens - previous.codegen.completion_tokens,
            ),
            planner=RoleUsage(
                calls=self.planner.calls - previous.planner.calls,
                prompt_tokens=self.planner.prompt_tokens - previous.planner.prompt_tokens,
                completion_tokens=self.planner.completion_tokens - previous.planner.completion_tokens,
            ),
        )


class LLM:
    # Total attempts = _MAX_ATTEMPTS. Backoff = 1s, 2s (only between attempts).
    # Kept small: the classifier already defaults survivors to real_gap on final
    # failure, so we don't want to burn a lot of wall clock chasing a dead API.
    _MAX_ATTEMPTS = 3
    _INITIAL_BACKOFF_S = 1.0

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self.usage = Usage()
        self._sleep = time.sleep  # test seam

    def complete(self, role: Role, *, system: str, user: str) -> LLMResponse:
        r: LLMRole = getattr(self._cfg.llm, role)
        api_key = os.environ.get(r.api_key_env) if r.api_key_env else None
        if r.api_key_env and not api_key:
            raise RuntimeError(
                f"role {role!r} needs env var {r.api_key_env} but it is not set"
            )

        kwargs: dict = {
            "model": r.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": r.temperature,
            "max_tokens": r.max_tokens,
        }
        if r.api_base:
            kwargs["api_base"] = r.api_base
        if api_key:
            kwargs["api_key"] = api_key

        last_exc: BaseException | None = None
        for attempt in range(self._MAX_ATTEMPTS):
            try:
                resp = litellm.completion(**kwargs)
                break
            except Exception as e:  # noqa: BLE001 -- litellm raises many types
                last_exc = e
                if attempt == self._MAX_ATTEMPTS - 1 or not _is_retryable(e):
                    raise
                self._sleep(self._INITIAL_BACKOFF_S * (2 ** attempt))
        else:  # pragma: no cover -- loop always break/raises
            raise RuntimeError("unreachable") from last_exc

        choice = resp["choices"][0]["message"]["content"]
        usage = resp.get("usage") or {}
        out = LLMResponse(
            text=choice or "",
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            model=r.model,
        )
        self.usage.record(role, out)
        return out
