"""Retry / backoff behavior for the LLM client.

We stub litellm.completion at the module level so no real network hits.
The tests care about behavior, not litellm internals:
    - transient rate-limit / timeout retries and eventually succeeds
    - permanent errors (e.g. auth) do NOT retry
    - after _MAX_ATTEMPTS failures the last exception surfaces to the caller
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mutagen.agent import llm as llm_mod
from mutagen.agent.llm import LLM, _is_retryable
from mutagen.config import AppConfig


def _cfg() -> AppConfig:
    cfg = AppConfig()
    # Kill env-var lookup for the codegen role so tests don't require API keys.
    cfg.llm.codegen.api_key_env = None
    cfg.llm.codegen.api_base = None
    return cfg


class _FakeRateLimitError(Exception):
    """Named exactly like litellm.RateLimitError so _is_retryable picks it up."""


_FakeRateLimitError.__name__ = "RateLimitError"


class _AuthError(Exception):
    """Not in the retry allow-list -> should not retry."""


def _mock_ok_response() -> dict:
    return {
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }


def test_is_retryable_recognizes_rate_limits() -> None:
    assert _is_retryable(_FakeRateLimitError("429 quota"))
    assert _is_retryable(TimeoutError("read timeout"))
    # Message-based match: covers exception classes we don't know by name.
    assert _is_retryable(Exception("upstream connection reset"))
    # Permanent error -> not retryable.
    assert not _is_retryable(_AuthError("bad api key"))


def test_llm_retries_transient_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def flaky(**_kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeRateLimitError("429 slow down")
        return _mock_ok_response()

    monkeypatch.setattr(llm_mod.litellm, "completion", flaky)
    slept: list[float] = []
    llm = LLM(_cfg())
    llm._sleep = slept.append  # capture backoff durations
    resp = llm.complete("codegen", system="s", user="u")
    assert resp.text == "hi"
    assert calls["n"] == 3
    # Two backoffs before the successful third call: 1s, 2s.
    assert slept == [1.0, 2.0]


def test_llm_stops_retrying_on_permanent_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def perm(**_kwargs):
        calls["n"] += 1
        raise _AuthError("bad api key")

    monkeypatch.setattr(llm_mod.litellm, "completion", perm)
    llm = LLM(_cfg())
    slept: list[float] = []
    llm._sleep = slept.append
    with pytest.raises(_AuthError):
        llm.complete("codegen", system="s", user="u")
    assert calls["n"] == 1  # no retry attempts
    assert slept == []


def test_llm_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def always_flaky(**_kwargs):
        calls["n"] += 1
        raise _FakeRateLimitError("429")

    monkeypatch.setattr(llm_mod.litellm, "completion", always_flaky)
    llm = LLM(_cfg())
    llm._sleep = lambda *_: None  # skip real sleeps
    with pytest.raises(_FakeRateLimitError):
        llm.complete("codegen", system="s", user="u")
    assert calls["n"] == LLM._MAX_ATTEMPTS
