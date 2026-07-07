"""Loop driver.

Phase 1: one-shot. Copy target -> workdir, generate T1 tests, run pytest,
run mutmut, return the report. No planner, no tier escalation, no plateau
detection. Later phases layer those on top of this same shape.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from mutagen.agent.llm import LLM
from mutagen.config import AppConfig
from mutagen.mutation.report import MutationReport
from mutagen.mutation.runner import run_mutmut
from mutagen.sandbox.executor import RunResult, run_pytest
from mutagen.testgen import tier1


@dataclass
class LoopResult:
    workdir: Path
    generated_tests: Path
    pytest_ok: bool
    pytest_result: RunResult
    report: MutationReport | None


def _prepare_workdir(target: Path, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    dest = workdir / "target.py"
    shutil.copyfile(target, dest)
    return dest


def one_shot(*, target: Path, workdir: Path, cfg: AppConfig, llm: LLM) -> LoopResult:
    _prepare_workdir(target, workdir)

    test_source = tier1.generate(llm, target_source=workdir / "target.py")
    tests_path = workdir / "test_generated.py"
    tests_path.write_text(test_source, encoding="utf-8")

    pytest_res = run_pytest(workdir, timeout_s=cfg.sandbox.pytest_timeout_s)
    pytest_ok = pytest_res.returncode == 0

    if not pytest_ok:
        return LoopResult(
            workdir=workdir,
            generated_tests=tests_path,
            pytest_ok=False,
            pytest_result=pytest_res,
            report=None,
        )

    report, _ = run_mutmut(
        workdir=workdir,
        target_rel="target.py",
        run_timeout_s=cfg.sandbox.mutmut_timeout_s,
        disabled_types=cfg.mutation.disabled_types,
    )
    return LoopResult(
        workdir=workdir,
        generated_tests=tests_path,
        pytest_ok=True,
        pytest_result=pytest_res,
        report=report,
    )
