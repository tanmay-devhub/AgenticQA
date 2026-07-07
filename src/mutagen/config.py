"""Central config (pydantic).

Two LLM ROLES, so we can send heavy codegen to a coder-specialist and reserve
a stronger reasoner for planning:

    codegen  -- writes pytest source. Default: Ollama Cloud qwen3-coder:480b-cloud
                (frontier open-source coder, 256K context).
    planner  -- decides which mutant to attack next and which tier/technique
                to reach for. Default: Gemini 2.5 Pro via litellm.

Both go through litellm, so any provider can be swapped by editing config only.
Nothing here is wired to real calls yet -- Phase 0 scaffolding.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LLMRole(BaseModel):
    """One LLM endpoint. litellm-style model string + optional base_url/api_key."""

    model: str
    api_base: str | None = None
    api_key_env: str | None = None
    temperature: float = 0.2
    max_tokens: int = 4096


class LLMConfig(BaseModel):
    codegen: LLMRole = Field(
        default_factory=lambda: LLMRole(
            model="ollama/qwen3-coder:480b-cloud",
            api_base="https://ollama.com",
            api_key_env="OLLAMA_API_KEY",
            temperature=0.2,
            max_tokens=8192,
        )
    )
    planner: LLMRole = Field(
        default_factory=lambda: LLMRole(
            model="gemini/gemini-2.5-pro",
            api_key_env="GEMINI_API_KEY",
            temperature=0.1,
            max_tokens=2048,
        )
    )


class SandboxLimits(BaseModel):
    backend: str = "subprocess"  # "subprocess" | "docker"
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
    max_rounds: int = 1        # Phase 1 = one-shot
    plateau_delta: float = 0.02
    wall_clock_s: int = 600


class AppConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    sandbox: SandboxLimits = Field(default_factory=SandboxLimits)
    mutation: MutationConfig = Field(default_factory=MutationConfig)
    loop: LoopBudget = Field(default_factory=LoopBudget)
