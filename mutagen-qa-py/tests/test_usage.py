from mutagen.agent.llm import LLMResponse, Usage
from mutagen.agent.testing import FakeLLM


def test_role_usage_accumulates_across_calls():
    llm = FakeLLM(responses={"codegen": ["one", "two"], "planner": ["p"]})
    llm.complete("codegen", system="s", user="uuuu")
    llm.complete("codegen", system="s", user="uuuu")
    llm.complete("planner", system="s", user="uuuu")

    assert llm.usage.codegen.calls == 2
    assert llm.usage.planner.calls == 1
    assert llm.usage.codegen.total_tokens == llm.usage.codegen.prompt_tokens + llm.usage.codegen.completion_tokens
    assert llm.usage.codegen.total_tokens > 0


def test_usage_delta_subtracts_from_snapshot():
    u = Usage()
    u.record("codegen", LLMResponse(text="", prompt_tokens=10, completion_tokens=5, model="x"))
    snap = u.snapshot()
    u.record("codegen", LLMResponse(text="", prompt_tokens=3, completion_tokens=2, model="x"))
    d = u.delta(snap)
    assert d.codegen.calls == 1
    assert d.codegen.prompt_tokens == 3
    assert d.codegen.completion_tokens == 2


def test_usage_handles_none_tokens_from_provider():
    u = Usage()
    u.record("planner", LLMResponse(text="", prompt_tokens=None, completion_tokens=None, model="x"))
    assert u.planner.calls == 1
    assert u.planner.total_tokens == 0
