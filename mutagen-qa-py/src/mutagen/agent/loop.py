"""Loop driver.

Phase 2: multi-round. Round 1 = T1 generation. Rounds 2..N feed classified
`real_gap` survivors from the planner to the T2 generator. Stops when kill
rate plateaus, budget hits, or no real survivors remain. `one_shot` is
retained as the `max_rounds=1` case so Phase 1 callers keep working.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.agent import debrief as debrief_mod
from mutagen.agent.classifier import classify_survivors
from mutagen.agent.llm import LLM, Usage
from mutagen.agent.planner import plan_specs
from mutagen.config import AppConfig
from mutagen.mutation.coverage import FileCoverage, load_coverage
from mutagen.mutation.report import MutationReport
from mutagen.mutation.runner import run_mutmut
from mutagen.sandbox.executor import RunResult, run_pytest
from mutagen.testgen import repair as repair_mod
from mutagen.testgen import tier1, tier2, tier3


@dataclass
class RoundResult:
    # NOTE: ``pytest_result`` / ``pytest_ok`` are the shared field names on the
    # wire, kept even for JS runs (which invoke ``node --test``, not pytest).
    # Renaming would ripple through the web dashboard templates, MCP server,
    # benchmark harness, and the JS mirror; the naming is now vocabulary, not
    # a runner assertion. Interpret both as "test-runner outcome".
    index: int                       # 1-based
    tier: int                        # 1 or 2
    tests_path: Path
    pytest_result: RunResult
    pytest_ok: bool
    report: MutationReport | None    # None if pytest failed and mutmut was skipped
    elapsed_s: float
    usage: Usage = field(default_factory=Usage)  # LLM tokens spent DURING this round
    repaired: bool = False           # true if we regenerated tests after a pytest failure
    coverage: FileCoverage | None = None  # coverage for target.py (best-effort)
    # The pytest run BEFORE any repair. Populated iff pytest failed and we
    # attempted a repair; useful for the round debrief so the file records
    # what the LLM's first output actually broke on.
    initial_pytest_result: RunResult | None = None
    # True when the codegen call returned no test functions (typically token
    # starvation on a reasoner model). pytest_result / report are sentinels
    # in that case: pytest was never invoked.
    no_codegen_output: bool = False

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "tier": self.tier,
            "tests_path": str(self.tests_path),
            "pytest_ok": self.pytest_ok,
            "repaired": self.repaired,
            "no_codegen_output": self.no_codegen_output,
            "elapsed_s": self.elapsed_s,
            "report": self.report.to_dict() if self.report else None,
            "coverage": {
                "missing_lines": self.coverage.missing_lines,
                "line_rate": self.coverage.line_rate,
            } if self.coverage else None,
            "usage": {
                "codegen": vars(self.usage.codegen),
                "planner": vars(self.usage.planner),
            },
        }


@dataclass
class LoopResult:
    workdir: Path
    rounds: list[RoundResult] = field(default_factory=list)
    stopped_reason: str = ""

    @property
    def total_usage(self) -> Usage:
        """Sum of per-round LLM spend across the whole run."""
        agg = Usage()
        for r in self.rounds:
            agg.codegen.calls += r.usage.codegen.calls
            agg.codegen.prompt_tokens += r.usage.codegen.prompt_tokens
            agg.codegen.completion_tokens += r.usage.codegen.completion_tokens
            agg.planner.calls += r.usage.planner.calls
            agg.planner.prompt_tokens += r.usage.planner.prompt_tokens
            agg.planner.completion_tokens += r.usage.planner.completion_tokens
        return agg

    @property
    def final_report(self) -> MutationReport | None:
        for r in reversed(self.rounds):
            if r.report is not None:
                return r.report
        return None

    @property
    def final_tests(self) -> Path | None:
        if not self.rounds:
            return None
        return self.rounds[-1].tests_path

    # Phase 1 back-compat properties (CLI still reads these on the one-shot path).
    @property
    def pytest_ok(self) -> bool:
        return bool(self.rounds) and self.rounds[-1].pytest_ok

    @property
    def pytest_result(self) -> RunResult | None:
        return self.rounds[-1].pytest_result if self.rounds else None

    @property
    def report(self) -> MutationReport | None:
        return self.final_report

    @property
    def generated_tests(self) -> Path | None:
        return self.final_tests

    def to_dict(self) -> dict:
        total = self.total_usage
        return {
            "workdir": str(self.workdir),
            "stopped_reason": self.stopped_reason,
            "rounds": [r.to_dict() for r in self.rounds],
            "total_usage": {
                "codegen": vars(total.codegen),
                "planner": vars(total.planner),
            },
            "final_kill_rate": self.final_report.kill_rate if self.final_report else None,
        }


def _prepare_workdir(target: Path, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    dest = workdir / "target.py"
    shutil.copyfile(target, dest)
    return dest


_NO_CODEGEN_STDERR = (
    "codegen returned no test functions; pytest and mutmut were skipped"
)


def _empty_codegen_round(*, index: int, tier: int, tests_path: Path) -> RoundResult:
    """Round record for the case where codegen produced no `def test_*`.

    Constructed instead of calling `_run_round`: running pytest against the
    empty file would silently rediscover the previous round's tests and make
    the failed round look successful. `pytest_result.returncode == -1` is the
    sentinel; the debrief and run.json call this out explicitly.
    """
    return RoundResult(
        index=index, tier=tier, tests_path=tests_path,
        pytest_result=RunResult(
            returncode=-1, stdout="", stderr=_NO_CODEGEN_STDERR, timed_out=False,
        ),
        pytest_ok=False, report=None, elapsed_s=0.0,
        no_codegen_output=True,
    )


def _persist_round(workdir: Path, r: RoundResult) -> None:
    (workdir / f"round_{r.index}_report.json").write_text(
        json.dumps(r.to_dict(), indent=2, default=str), encoding="utf-8"
    )


def _persist_final(workdir: Path, result: LoopResult) -> None:
    (workdir / "run.json").write_text(
        json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8"
    )


def _run_round(
    *,
    workdir: Path,
    tests_path: Path,
    cfg: AppConfig,
    index: int,
    tier: int,
    llm: LLM | None = None,
) -> RoundResult:
    """Run pytest, and if it fails, give the LLM ONE chance to repair the tests.

    ``llm`` is optional so orchestration tests can pass ``None`` and observe the
    raw pytest failure without triggering a repair attempt.
    """
    t0 = time.monotonic()
    backend = cfg.sandbox.backend
    pytest_res = run_pytest(
        workdir, timeout_s=cfg.sandbox.pytest_timeout_s,
        coverage_source="target", backend=backend,
    )
    pytest_ok = pytest_res.returncode == 0
    repaired = False
    initial_pytest_result: RunResult | None = None

    if not pytest_ok and llm is not None:
        # Snapshot the failing pytest result BEFORE we overwrite it with the
        # post-repair result -- the debrief file wants both sides.
        initial_pytest_result = pytest_res
        target_src = workdir / "target.py"
        # Up to MAX_REPAIR_ATTEMPTS shots; attempt 1 bumps temperature +
        # nudges the model to reconsider its assumptions instead of
        # re-emitting the same broken assertions.
        for attempt in range(repair_mod.MAX_REPAIR_ATTEMPTS):
            fixed = repair_mod.repair(
                llm,
                target_source=target_src,
                tests_path=tests_path,
                pytest_stderr=(pytest_res.stderr or "") + "\n" + (pytest_res.stdout or ""),
                attempt=attempt,
            )
            tests_path.write_text(fixed, encoding="utf-8")
            pytest_res = run_pytest(
                workdir, timeout_s=cfg.sandbox.pytest_timeout_s, backend=backend,
            )
            pytest_ok = pytest_res.returncode == 0
            repaired = True
            if pytest_ok:
                break

    report: MutationReport | None = None
    if pytest_ok:
        report, _ = run_mutmut(
            workdir=workdir,
            target_rel="target.py",
            run_timeout_s=cfg.sandbox.mutmut_timeout_s,
            disabled_types=cfg.mutation.disabled_types,
            backend=backend,
        )
    coverage = load_coverage(workdir).get("target.py") if pytest_ok else None
    return RoundResult(
        index=index,
        tier=tier,
        tests_path=tests_path,
        pytest_result=pytest_res,
        pytest_ok=pytest_ok,
        report=report,
        elapsed_s=time.monotonic() - t0,
        repaired=repaired,
        coverage=coverage,
        initial_pytest_result=initial_pytest_result,
    )


def one_shot(*, target: Path, workdir: Path, cfg: AppConfig, llm: LLM) -> LoopResult:
    """Phase-1 shim: force max_rounds=1 regardless of config."""
    return run_loop(target=target, workdir=workdir, cfg=cfg, llm=llm, max_rounds=1)


def run_loop(
    *,
    target: Path,
    workdir: Path,
    cfg: AppConfig,
    llm: LLM,
    max_rounds: int | None = None,
) -> LoopResult:
    """Run generate -> pytest -> mutmut -> classify -> plan for up to N rounds."""
    max_rounds = max_rounds if max_rounds is not None else cfg.loop.max_rounds
    result = LoopResult(workdir=workdir)
    _prepare_workdir(target, workdir)
    target_src = workdir / "target.py"
    started = time.monotonic()

    try:
        return _drive_loop(
            result=result, target_src=target_src, workdir=workdir, cfg=cfg,
            llm=llm, max_rounds=max_rounds, started=started,
        )
    except BaseException as e:  # noqa: BLE001 -- record then re-raise
        # Set stopped_reason before persistence so run.json distinguishes a
        # crash from a fresh workdir. Re-raised at the end.
        result.stopped_reason = f"crashed: {type(e).__name__}: {e}"
        raise
    finally:
        _persist_final(workdir, result)


def _drive_loop(
    *,
    result: LoopResult,
    target_src: Path,
    workdir: Path,
    cfg: AppConfig,
    llm: LLM,
    max_rounds: int,
    started: float,
) -> LoopResult:
    # Round 1: T1.
    usage_before = llm.usage.snapshot()
    t1_source = tier1.generate(llm, target_source=target_src)
    t1_path = workdir / "test_round_1.py"
    t1_path.write_text(t1_source, encoding="utf-8")

    if not tier1.has_tests(t1_source):
        round1 = _empty_codegen_round(index=1, tier=1, tests_path=t1_path)
        round1.usage = llm.usage.delta(usage_before)
        result.rounds.append(round1)
        _persist_round(workdir, round1)
        debrief_mod.write_round_body(workdir, round1)
        result.stopped_reason = (
            "codegen produced no tests in round 1 "
            "(raise codegen max_tokens or switch model)"
        )
        return result

    round1 = _run_round(workdir=workdir, tests_path=t1_path, cfg=cfg, index=1, tier=1, llm=llm)
    round1.usage = llm.usage.delta(usage_before)
    result.rounds.append(round1)
    _persist_round(workdir, round1)
    debrief_mod.write_round_body(workdir, round1)

    if not round1.pytest_ok:
        result.stopped_reason = "pytest failed in round 1"
        return result
    if max_rounds <= 1:
        result.stopped_reason = "max_rounds reached"
        return result
    assert round1.report is not None
    if not round1.report.survivors:
        result.stopped_reason = "no survivors after round 1"
        return result

    prev_kill = round1.report.kill_rate
    t3_used = False    # only one T3 escalation attempt per run
    next_tier = 2

    for i in range(2, max_rounds + 1):
        if time.monotonic() - started > cfg.loop.wall_clock_s:
            result.stopped_reason = "wall-clock budget exceeded"
            return result

        usage_before = llm.usage.snapshot()
        prev = result.rounds[-1].report
        assert prev is not None
        classified = classify_survivors(
            llm,
            target_source=target_src,
            survivors=prev.survivors,
            cache_dir=workdir / ".mutagen",
        )
        prev_cov = result.rounds[-1].coverage
        missing = prev_cov.missing_lines if prev_cov else None
        specs = plan_specs(classified, missing_lines=missing)
        tier = next_tier
        # Record what round i-1 is handing off to round i, even if the
        # planner produced no specs (that itself is useful debrief signal).
        debrief_mod.append_handoff(
            workdir, result.rounds[-1].index,
            next_round_index=i, next_tier=tier,
            classified=classified, specs=specs,
        )
        if not specs:
            result.stopped_reason = "no real_gap survivors to plan against"
            return result

        if tier == 2:
            source = tier2.generate(llm, target_source=target_src, specs=specs)
        else:
            source = tier3.generate(llm, target_source=target_src, specs=specs)
            t3_used = True
        tests_path = workdir / f"test_round_{i}.py"
        tests_path.write_text(source, encoding="utf-8")

        if not tier1.has_tests(source):
            r = _empty_codegen_round(index=i, tier=tier, tests_path=tests_path)
            r.usage = llm.usage.delta(usage_before)
            result.rounds.append(r)
            _persist_round(workdir, r)
            debrief_mod.write_round_body(workdir, r)
            result.stopped_reason = (
                f"codegen produced no tests in round {i} "
                "(raise codegen max_tokens or switch model)"
            )
            return result

        r = _run_round(workdir=workdir, tests_path=tests_path, cfg=cfg, index=i, tier=tier, llm=llm)
        r.usage = llm.usage.delta(usage_before)
        result.rounds.append(r)
        _persist_round(workdir, r)
        debrief_mod.write_round_body(workdir, r)

        if not r.pytest_ok:
            result.stopped_reason = f"pytest failed in round {i}"
            return result
        assert r.report is not None
        if not r.report.survivors:
            result.stopped_reason = "no survivors remaining"
            return result

        delta = r.report.kill_rate - prev_kill
        if delta < cfg.loop.plateau_delta:
            # Give T3 exactly one shot at killing the residue before we give up.
            if not t3_used:
                next_tier = 3
                prev_kill = r.report.kill_rate  # do NOT reset delta baseline
                continue
            result.stopped_reason = f"plateau after T3 (delta={delta:+.3f} < {cfg.loop.plateau_delta})"
            return result
        prev_kill = r.report.kill_rate
        next_tier = 2  # any progress -> back to T2

    result.stopped_reason = "max_rounds reached"
    return result
