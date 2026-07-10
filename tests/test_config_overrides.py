"""Verify env-var overrides can swap out the planner without code changes."""

from mutagen.config import AppConfig


def test_default_config_has_expected_models():
    cfg = AppConfig()
    assert cfg.llm.codegen.model.startswith("ollama/")
    assert cfg.llm.planner.model.startswith("gemini/")


def test_planner_env_override_swaps_model(monkeypatch):
    monkeypatch.setenv("MUTAGEN_PLANNER_MODEL", "ollama/qwen3-coder:480b-cloud")
    monkeypatch.setenv("MUTAGEN_PLANNER_API_BASE", "https://ollama.com")
    monkeypatch.setenv("MUTAGEN_PLANNER_API_KEY_ENV", "OLLAMA_API_KEY")

    cfg = AppConfig()
    assert cfg.llm.planner.model == "ollama/qwen3-coder:480b-cloud"
    assert cfg.llm.planner.api_base == "https://ollama.com"
    assert cfg.llm.planner.api_key_env == "OLLAMA_API_KEY"


def test_codegen_env_override_swaps_model(monkeypatch):
    monkeypatch.setenv("MUTAGEN_CODEGEN_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("MUTAGEN_CODEGEN_API_KEY_ENV", "OPENAI_API_KEY")

    cfg = AppConfig()
    assert cfg.llm.codegen.model == "openai/gpt-4o-mini"
    assert cfg.llm.codegen.api_key_env == "OPENAI_API_KEY"
