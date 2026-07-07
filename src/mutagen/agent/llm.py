"""Provider-agnostic LLM client (litellm-backed).

Two roles from config:
    codegen  -- default: Ollama Cloud `qwen3-coder:480b-cloud`.
    planner  -- default: `gemini/gemini-2.5-pro`.

Provider swaps are one config edit. Zero-cost path = point `codegen` at a
local Ollama model, e.g. `ollama/qwen3-coder:30b`, without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

import litellm

from mutagen.config import AppConfig, LLMRole

Role = Literal["codegen", "planner"]


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    model: str


class LLM:
    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg

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

        resp = litellm.completion(**kwargs)
        choice = resp["choices"][0]["message"]["content"]
        usage = resp.get("usage") or {}
        return LLMResponse(
            text=choice or "",
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            model=r.model,
        )
