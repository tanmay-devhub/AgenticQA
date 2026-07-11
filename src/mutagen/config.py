"""Central config (pydantic).

Two LLM ROLES, so we can send heavy codegen to a coder-specialist and reserve
a stronger reasoner for planning:

    codegen  -- writes pytest source. Default: Ollama Cloud qwen3-coder:480b-cloud
                (frontier open-source coder, 256K context).
    planner  -- decides which mutant to attack next and which tier/technique
                to reach for. Default: Gemini 2.5 Pro via litellm.

Both go through litellm, so any provider can be swapped by editing config or by
setting env vars (see below) -- no code changes needed.

Env-var overrides (checked at ``AppConfig()`` construction):
    MUTAGEN_CODEGEN_MODEL / MUTAGEN_PLANNER_MODEL
    MUTAGEN_CODEGEN_API_BASE / MUTAGEN_PLANNER_API_BASE
    MUTAGEN_CODEGEN_API_KEY_ENV / MUTAGEN_PLANNER_API_KEY_ENV
This is the escape hatch for outages: if Gemini is rate-limited, point the
planner at Ollama Cloud without editing code.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field


class LLMRole(BaseModel):
    """One LLM endpoint. litellm-style model string + optional base_url/api_key."""

    model: str
    api_base: str | None = None
    api_key_env: str | None = None
    temperature: float = 0.2
    max_tokens: int = 4096


def _apply_env_overrides(role: LLMRole, prefix: str) -> LLMRole:
    """Overlay MUTAGEN_<PREFIX>_{MODEL,API_BASE,API_KEY_ENV} onto ``role``."""
    m = os.environ.get(f"MUTAGEN_{prefix}_MODEL")
    b = os.environ.get(f"MUTAGEN_{prefix}_API_BASE")
    k = os.environ.get(f"MUTAGEN_{prefix}_API_KEY_ENV")
    if m:
        role.model = m
    if b:
        role.api_base = b
    if k:
        role.api_key_env = k
    return role


class LLMConfig(BaseModel):
    codegen: LLMRole = Field(
        default_factory=lambda: _apply_env_overrides(
            LLMRole(
                model="ollama/qwen3-coder:480b-cloud",
                api_base="https://ollama.com",
                api_key_env="OLLAMA_API_KEY",
                temperature=0.2,
                max_tokens=8192,
            ),
            "CODEGEN",
        )
    )
    planner: LLMRole = Field(
        default_factory=lambda: _apply_env_overrides(
            LLMRole(
                model="gemini/gemini-2.5-pro",
                api_key_env="GEMINI_API_KEY",
                temperature=0.1,
                max_tokens=2048,
            ),
            "PLANNER",
        )
    )


class SandboxLimits(BaseModel):
    # Env var MUTAGEN_SANDBOX_BACKEND=docker flips execution into the
    # container backend without any code or CLI-flag change.
    backend: str = Field(
        default_factory=lambda: os.environ.get("MUTAGEN_SANDBOX_BACKEND", "subprocess"),
    )  # "subprocess" | "docker"
    pytest_timeout_s: int = 30
    mutmut_timeout_s: int = 120
    memory_mb: int | None = None


class MutationConfig(BaseModel):
    max_mutants: int | None = None
    per_mutant_timeout_s: int = 10
    # mutmut mutation types to skip. Defaults to string/fstring because pure
    # string-literal mutations mostly change error-message wording, which our
    # generator (correctly) refuses to assert on. Set to [] when strings ARE
    # behavior (template renderers, URL builders, format-string logic).
    disabled_types: list[str] = Field(default_factory=lambda: ["string", "fstring"])


class LoopBudget(BaseModel):
    # Phase 2: multi-round. Round 1 = T1; rounds 2..N = T2 driven by classified
    # survivors. Set to 1 (or pass `--max-rounds 1`) for Phase-1 one-shot mode.
    max_rounds: int = 3
    plateau_delta: float = 0.02
    wall_clock_s: int = 600


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    sandbox: SandboxLimits = Field(default_factory=SandboxLimits)
    mutation: MutationConfig = Field(default_factory=MutationConfig)
    loop: LoopBudget = Field(default_factory=LoopBudget)
