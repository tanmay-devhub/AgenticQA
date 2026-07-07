"""Run mutmut on (target, test_suite) and build a MutationReport.

Phase 1: invoke mutmut CLI in a workdir. For counts, query `result-ids
<status>` per bucket (killed / survived / timeout / suspicious / skipped) --
more reliable than scraping the progress bar. For per-survivor diffs, call
`mutmut show <id>`. Cache lives in the workdir.

Mutmut defaults spawn `python -m pytest`, which on Windows can hit the Store
shim if the venv isn't first on PATH. We pass `--runner` with `sys.executable`
to make it deterministic.
"""

from __future__ import annotations

import sys
from pathlib import Path

from mutagen.mutation.report import Mutant, MutationReport
from mutagen.sandbox.executor import RunResult, run

_STATUSES = ("killed", "survived", "timeout", "suspicious", "skipped")


def _mutmut(args: list[str], *, cwd: Path, timeout_s: int) -> RunResult:
    return run([sys.executable, "-m", "mutmut", *args], cwd=cwd, timeout_s=timeout_s)


def _result_ids(status: str, *, cwd: Path, timeout_s: int) -> list[str]:
    res = _mutmut(["result-ids", status], cwd=cwd, timeout_s=timeout_s)
    if res.returncode != 0:
        return []
    ids: list[str] = []
    for line in res.stdout.splitlines():
        for tok in line.replace(",", " ").split():
            tok = tok.strip()
            if tok:
                ids.append(tok)
    return ids


def _fetch_diff(mutant_id: str, *, cwd: Path, timeout_s: int) -> str | None:
    res = _mutmut(["show", mutant_id], cwd=cwd, timeout_s=timeout_s)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def run_mutmut(
    *,
    workdir: Path,
    target_rel: str,
    run_timeout_s: int,
    per_call_timeout_s: int = 60,
    disabled_types: list[str] | None = None,
) -> tuple[MutationReport, RunResult]:
    """Run mutmut in ``workdir`` against ``target_rel`` and return a report."""
    runner_cmd = f'"{sys.executable}" -m pytest -x --assert=plain'
    args = [
        "run",
        "--paths-to-mutate", target_rel,
        "--tests-dir", ".",
        "--runner", runner_cmd,
        "--simple-output",
        "--no-progress",
    ]
    if disabled_types:
        args.extend(["--disable-mutation-types", ",".join(disabled_types)])
    run_res = _mutmut(args, cwd=workdir, timeout_s=run_timeout_s)

    counts: dict[str, int] = {}
    survivor_ids: list[str] = []
    for status in _STATUSES:
        ids = _result_ids(status, cwd=workdir, timeout_s=per_call_timeout_s)
        counts[status] = len(ids)
        if status == "survived":
            survivor_ids = ids

    survivors: list[Mutant] = []
    for mid in survivor_ids:
        m = Mutant(id=mid, file=None, line=None, status="survived")
        m.diff = _fetch_diff(mid, cwd=workdir, timeout_s=per_call_timeout_s)
        survivors.append(m)

    total = sum(counts.values())
    report = MutationReport(
        total=total,
        killed=counts["killed"],
        survived=counts["survived"],
        timeout=counts["timeout"],
        suspicious=counts["suspicious"],
        skipped=counts["skipped"],
        survivors=survivors,
        disabled_types=list(disabled_types or []),
    )
    return report, run_res
