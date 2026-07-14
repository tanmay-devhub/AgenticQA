"""FastMCP server exposing three tools:

    qa_generate_tests(target_path, tier=1) -> {source, tier, tokens}
        Run tier-1/2/3 generator once and return the pytest source. No
        pytest, no mutmut -- for callers that want raw generation only.

    qa_mutation_score(target_path, tests_source) -> {killed, survived, ...}
        Copy the target and hand-written suite into an isolated workdir,
        run pytest, then mutmut. Returns the same MutationReport shape the
        CLI prints, as a dict.

    qa_run_loop(target_path, max_rounds=3, workdir_name=None) -> {...}
        Full multi-round loop. Returns the same JSON the dashboard displays
        plus the workdir path so downstream tools can inspect generated
        files.

Every tool is a thin wrapper over the CLI's underlying functions. If it works
from the CLI, it works from MCP -- the tools re-use ``run_loop``, ``run_pytest``,
``run_mutmut``, ``tier1``/``tier2``/``tier3`` unchanged.

The server is constructed via ``create_server()`` (dependency injection for
``runs_root`` + optional ``llm_factory``) so tests can call it in-memory via
FastMCP's ``call_tool`` without ever opening a real transport.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP

from mutagen.agent.llm import LLM
from mutagen.agent.loop import run_loop
from mutagen.config import AppConfig
from mutagen.mutation.runner import run_mutmut
from mutagen.sandbox.executor import run_pytest
from mutagen.testgen import tier1, tier2, tier3


def _default_llm_factory() -> LLM:
    return LLM(AppConfig())


def _resolve_target(target_path: str) -> Path:
    p = Path(target_path).expanduser().resolve()
    if not p.is_file():
        raise ValueError(f"target_path not found or not a file: {target_path}")
    if p.suffix != ".py":
        raise ValueError(f"target_path must be a .py file: {target_path}")
    return p


def _slug(name: str) -> str:
    """Timestamped workdir slug: ``<name>-YYYYMMDD-HHMMSS``."""
    return f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"


def create_server(
    runs_root: Path,
    *,
    llm_factory: Callable[[], LLM] = _default_llm_factory,
) -> FastMCP:
    """Build the MCP server rooted at ``runs_root``.

    ``llm_factory`` returns a fresh LLM per call so token accounting doesn't
    leak between tool invocations. Tests inject a factory that returns a
    ``FakeLLM``.
    """
    runs_root = runs_root.resolve()
    runs_root.mkdir(parents=True, exist_ok=True)

    server = FastMCP(
        name="mutagen",
        instructions=(
            "Mutation-guided test generation for Python. Call qa_run_loop for "
            "the full agentic loop (generate -> pytest -> mutmut -> classify -> "
            "iterate). Use qa_generate_tests for one-shot generation without "
            "scoring, or qa_mutation_score to grade an existing suite."
        ),
    )

    @server.tool()
    def qa_generate_tests(target_path: str, tier: int = 1) -> dict:
        """Generate a pytest suite for the target using the requested tier.

        Args:
            target_path: absolute path to a single .py file. Docstrings on
                the target's public functions drive the generator's oracle.
            tier: 1 = happy-path parameterized, 2 = boundary/error (falls
                back to T1 shape when no survivor specs are supplied),
                3 = Hypothesis property-based.

        Returns:
            ``{"source": "<pytest source>", "tier": 1|2|3,
              "codegen_tokens": N}``.
        """
        if tier not in (1, 2, 3):
            raise ValueError("tier must be 1, 2, or 3")
        tgt = _resolve_target(target_path)
        llm = llm_factory()
        if tier == 1:
            src = tier1.generate(llm, target_source=tgt)
        elif tier == 2:
            src = tier2.generate(llm, target_source=tgt, specs=[])
        else:
            src = tier3.generate(llm, target_source=tgt, specs=[])
        return {
            "source": src,
            "tier": tier,
            "codegen_tokens": llm.usage.codegen.total_tokens,
        }

    @server.tool()
    def qa_mutation_score(target_path: str, tests_source: str) -> dict:
        """Score a hand-written suite against a target via pytest + mutmut.

        Args:
            target_path: absolute path to the .py file under test.
            tests_source: the pytest module contents as a string (not a path).
                Must ``from target import ...`` -- the target is copied to
                ``target.py`` in an isolated workdir.

        Returns:
            ``MutationReport.to_dict()`` merged with ``pytest_ok`` and
            ``workdir``. ``pytest_ok=False`` if the suite doesn't even run;
            in that case ``pytest_stdout``/``pytest_stderr`` are included.
        """
        tgt = _resolve_target(target_path)
        wd = runs_root / _slug(f"score-{tgt.stem}")
        wd.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tgt, wd / "target.py")
        (wd / "test_suite.py").write_text(tests_source, encoding="utf-8")

        cfg = AppConfig()
        pytest_res = run_pytest(wd, timeout_s=cfg.sandbox.pytest_timeout_s)
        if pytest_res.returncode != 0:
            return {
                "pytest_ok": False,
                "pytest_stdout": pytest_res.stdout,
                "pytest_stderr": pytest_res.stderr,
                "workdir": str(wd),
            }
        report, _ = run_mutmut(
            workdir=wd,
            target_rel="target.py",
            run_timeout_s=cfg.sandbox.mutmut_timeout_s,
            disabled_types=cfg.mutation.disabled_types,
        )
        return {"pytest_ok": True, "workdir": str(wd), **report.to_dict()}

    @server.tool()
    def qa_run_loop(
        target_path: str,
        max_rounds: int = 3,
        workdir_name: str | None = None,
    ) -> dict:
        """Full generate -> pytest -> mutmut -> classify -> plan loop.

        Args:
            target_path: absolute path to a .py file.
            max_rounds: cap on iterations (default 3). Round 1 = T1; later
                rounds = T2 driven by classified survivors. Plateau triggers
                a one-shot T3 escalation.
            workdir_name: optional folder name under runs_root. Auto-slugged
                from the target if omitted.

        Returns:
            The same ``run.json`` shape the dashboard displays, plus a
            ``workdir`` key. Point ``mutagen web -r <runs_root>`` at the
            same folder to browse results interactively.
        """
        tgt = _resolve_target(target_path)
        name = workdir_name or _slug(tgt.stem)
        wd = runs_root / name

        llm = llm_factory()
        cfg = AppConfig()
        result = run_loop(target=tgt, workdir=wd, cfg=cfg, llm=llm, max_rounds=max_rounds)
        payload = result.to_dict()
        payload["workdir"] = str(wd)
        return payload

    return server
