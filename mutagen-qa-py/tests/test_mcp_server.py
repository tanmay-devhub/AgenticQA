"""In-memory MCP server tests via FastMCP.call_tool.

FastMCP exposes ``list_tools`` / ``call_tool`` as coroutines you can drive
without a transport, which is exactly what tests need: no subprocess, no
stdio pipe, no network. We inject a FakeLLM factory so no real API is hit.

We stub ``run_pytest`` / ``run_mutmut`` inside the loop module (same pattern
as the orchestration tests) so ``qa_run_loop`` and ``qa_mutation_score``
resolve in milliseconds.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mutagen.agent import loop as loop_mod
from mutagen.agent.testing import FakeLLM
from mutagen.mcp import create_server
from mutagen.mcp import server as server_mod
from mutagen.mutation.report import MutationReport
from mutagen.sandbox.executor import RunResult


TARGET_SRC = '"""Doubles its input."""\n\ndef double(x: int) -> int:\n    return x * 2\n'


def _ok() -> RunResult:
    return RunResult(returncode=0, stdout="", stderr="", timed_out=False)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _decode(result):
    """FastMCP call_tool returns (content_blocks, structured_content) since 1.x.

    We only need the structured dict; unwrap defensively so schema changes
    don't take the whole suite with them.
    """
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, dict):
        return result
    # Fallback: pull text from first content block.
    return json.loads(result[0].text) if result else {}


@pytest.fixture
def target(tmp_path: Path) -> Path:
    p = tmp_path / "double.py"
    p.write_text(TARGET_SRC, encoding="utf-8")
    return p


@pytest.fixture
def server_and_llm(tmp_path: Path):
    """Fresh server + a FakeLLM the test can inspect after calls."""
    llm = FakeLLM(responses={
        "codegen": [
            "def test_r1(): assert True\n",
            "def test_r2(): assert True\n",
            "def test_r3(): assert True\n",
        ],
        "planner": ['{"verdict":"real_gap","reason":"gap"}'] * 5,
    })
    server = create_server(tmp_path / "runs", llm_factory=lambda: llm)
    return server, llm


def test_list_tools_exposes_qa_surface(server_and_llm) -> None:
    server, _ = server_and_llm
    tools = _run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {"qa_generate_tests", "qa_mutation_score", "qa_run_loop"}


def test_generate_tests_returns_source(server_and_llm, target) -> None:
    server, _ = server_and_llm
    out = _decode(_run(server.call_tool("qa_generate_tests",
                                        {"target_path": str(target), "tier": 1})))
    assert out["tier"] == 1
    assert "def test_r1" in out["source"]
    assert out["codegen_tokens"] > 0


def test_generate_tests_rejects_bad_tier(server_and_llm, target) -> None:
    server, _ = server_and_llm
    with pytest.raises(Exception) as excinfo:
        _run(server.call_tool("qa_generate_tests",
                              {"target_path": str(target), "tier": 9}))
    assert "tier" in str(excinfo.value).lower()


def test_generate_tests_rejects_missing_target(server_and_llm) -> None:
    server, _ = server_and_llm
    with pytest.raises(Exception) as excinfo:
        _run(server.call_tool("qa_generate_tests",
                              {"target_path": "/does/not/exist.py"}))
    assert "not found" in str(excinfo.value).lower()


def test_run_loop_returns_run_json_shape(server_and_llm, target, monkeypatch) -> None:
    server, llm = server_and_llm

    monkeypatch.setattr(loop_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(
        loop_mod, "run_mutmut",
        lambda **_kw: (MutationReport(total=5, killed=5, survived=0, survivors=[]), _ok()),
    )

    out = _decode(_run(server.call_tool("qa_run_loop",
                                        {"target_path": str(target),
                                         "max_rounds": 1,
                                         "workdir_name": "mcp-test-run"})))
    assert out["final_kill_rate"] == 1.0
    assert out["stopped_reason"]
    assert out["workdir"].endswith("mcp-test-run")
    # workdir was actually created and holds artifacts.
    wd = Path(out["workdir"])
    assert (wd / "run.json").exists()
    assert (wd / "target.py").exists()


def test_mutation_score_reports_pytest_failure(server_and_llm, target, monkeypatch) -> None:
    """When the caller's suite doesn't run, mutmut is skipped and pytest_ok=False."""
    server, _ = server_and_llm
    monkeypatch.setattr(
        server_mod, "run_pytest",
        lambda *_a, **_kw: RunResult(returncode=1, stdout="collect err", stderr="e", timed_out=False),
    )
    out = _decode(_run(server.call_tool("qa_mutation_score",
                                        {"target_path": str(target),
                                         "tests_source": "def test_broken(: pass\n"})))
    assert out["pytest_ok"] is False
    assert "collect err" in out["pytest_stdout"]


def test_mutation_score_reports_kill_rate_on_success(server_and_llm, target, monkeypatch) -> None:
    server, _ = server_and_llm
    monkeypatch.setattr(server_mod, "run_pytest", lambda *_a, **_kw: _ok())
    monkeypatch.setattr(
        server_mod, "run_mutmut",
        lambda **_kw: (MutationReport(total=4, killed=3, survived=1, survivors=[]), _ok()),
    )
    out = _decode(_run(server.call_tool(
        "qa_mutation_score",
        {"target_path": str(target),
         "tests_source": "from target import double\n\ndef test_double(): assert double(2)==4\n"},
    )))
    assert out["pytest_ok"] is True
    assert out["killed"] == 3
    assert out["survived"] == 1
    assert out["kill_rate"] == 0.75
