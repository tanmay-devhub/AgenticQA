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

import re
import sys
from pathlib import Path

from mutagen.mutation.report import Mutant, MutationKind, MutationReport
from mutagen.sandbox.executor import Backend, RunResult, run

_STATUSES = ("killed", "survived", "timeout", "suspicious", "skipped")

_HUNK_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@")

# Presence-only regexes for classifying which token category changed between
# the `-` and `+` sides of a unified diff. Numeric / bool / None literals are
# stripped to a placeholder BEFORE the operator regexes run, so that a `-1` ->
# `2` mutation classifies as `constant`, not `arithmetic`.
_LIT_RE = re.compile(r"-?\b\d+(?:\.\d+)?\b|\bTrue\b|\bFalse\b|\bNone\b")
_COMP_RE = re.compile(r"(<=?|>=?|==|!=|\bis(?:\s+not)?\b|\b(?:not\s+)?in\b)")
_BOOL_RE = re.compile(r"\b(?:and|or|not)\b")
_ARITH_RE = re.compile(r"[+\-*/%](?![=<>])")
_KW_RE = re.compile(r"\b(?:break|continue|pass)\b")
_RETURN_RE = re.compile(r"\breturn\b")


def _diff_pm_lines(diff: str) -> tuple[list[str], list[str]]:
    """Return ('-' body lines, '+' body lines) with headers filtered out."""
    minus: list[str] = []
    plus: list[str] = []
    for raw in diff.splitlines():
        if raw.startswith("---") or raw.startswith("+++"):
            continue
        if raw.startswith("-"):
            minus.append(raw[1:])
        elif raw.startswith("+"):
            plus.append(raw[1:])
    return minus, plus


def _classify_diff(diff: str) -> MutationKind:
    """Infer a coarse mutation kind from a unified diff.

    Presence-only heuristic: strip numeric/bool/None literals, then compare the
    set of comparison / boolean / arithmetic / keyword tokens on each side. The
    first category whose token-set differs wins. Literal-only changes fall
    through to ``constant``. ``return`` is checked last so it only wins when
    nothing more specific applies.
    """
    minus, plus = _diff_pm_lines(diff)
    if not minus and not plus:
        return "other"

    m = "\n".join(minus)
    p = "\n".join(plus)

    m_lits = sorted(_LIT_RE.findall(m))
    p_lits = sorted(_LIT_RE.findall(p))
    m_no_lit = _LIT_RE.sub("N", m)
    p_no_lit = _LIT_RE.sub("N", p)

    def _same(pat: re.Pattern[str]) -> bool:
        return set(pat.findall(m_no_lit)) == set(pat.findall(p_no_lit))

    if not _same(_COMP_RE):
        return "comparison"
    if not _same(_BOOL_RE):
        return "boolean"
    if not _same(_ARITH_RE):
        return "arithmetic"
    # Return-value substitution (`return x` -> `return None`) reads as `return`,
    # not `constant`, so the planner routes it to "assert the return value" rather
    # than "pin a numeric literal". Numeric-only return swaps (`return 1` -> `2`)
    # fall through to `constant` since m_no_lit == p_no_lit after literal-stripping.
    if _RETURN_RE.search(m_no_lit) and _RETURN_RE.search(p_no_lit) and m_no_lit != p_no_lit:
        return "return"
    if m_lits != p_lits:
        return "constant"
    if not _same(_KW_RE):
        return "keyword"
    if "(" in m_no_lit or "(" in p_no_lit:
        return "call"
    return "other"


def _parse_file_and_line(diff: str) -> tuple[str | None, int | None]:
    """Extract file path and the first modified line number (new-side)."""
    file: str | None = None
    line: int | None = None
    new_lineno: int | None = None
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].strip().split("\t", 1)[0]
            if path.startswith("a/") or path.startswith("b/"):
                path = path[2:]
            file = path or file
            continue
        m = _HUNK_RE.match(raw)
        if m:
            new_lineno = int(m.group(1))
            continue
        if new_lineno is None:
            continue
        first = raw[:1] if raw else " "
        if first == "+" and line is None:
            line = new_lineno
        if first in ("+", " "):
            new_lineno += 1
        # '-' lines do not advance the new-side counter
    return file, line


def _python_exe(backend: Backend) -> str:
    """Host Python for subprocess; PATH-resolved ``python`` inside the container."""
    return sys.executable if backend == "subprocess" else "python"


def _mutmut(args: list[str], *, cwd: Path, timeout_s: int, backend: Backend) -> RunResult:
    return run(
        [_python_exe(backend), "-m", "mutmut", *args],
        cwd=cwd, timeout_s=timeout_s, backend=backend,
    )


def _result_ids(status: str, *, cwd: Path, timeout_s: int, backend: Backend) -> list[str]:
    res = _mutmut(["result-ids", status], cwd=cwd, timeout_s=timeout_s, backend=backend)
    if res.returncode != 0:
        return []
    ids: list[str] = []
    for line in res.stdout.splitlines():
        for tok in line.replace(",", " ").split():
            tok = tok.strip()
            if tok:
                ids.append(tok)
    return ids


def _fetch_diff(mutant_id: str, *, cwd: Path, timeout_s: int, backend: Backend) -> str | None:
    res = _mutmut(["show", mutant_id], cwd=cwd, timeout_s=timeout_s, backend=backend)
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
    backend: Backend = "subprocess",
) -> tuple[MutationReport, RunResult]:
    """Run mutmut in ``workdir`` against ``target_rel`` and return a report."""
    py = _python_exe(backend)
    runner_cmd = f'"{py}" -m pytest -x --assert=plain'
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
    run_res = _mutmut(args, cwd=workdir, timeout_s=run_timeout_s, backend=backend)

    counts: dict[str, int] = {}
    survivor_ids: list[str] = []
    for status in _STATUSES:
        ids = _result_ids(status, cwd=workdir, timeout_s=per_call_timeout_s, backend=backend)
        counts[status] = len(ids)
        if status == "survived":
            survivor_ids = ids

    survivors: list[Mutant] = []
    for mid in survivor_ids:
        diff = _fetch_diff(mid, cwd=workdir, timeout_s=per_call_timeout_s, backend=backend)
        file, line = (None, None)
        kind: MutationKind = "other"
        if diff:
            file, line = _parse_file_and_line(diff)
            kind = _classify_diff(diff)
        survivors.append(
            Mutant(id=mid, file=file, line=line, status="survived", diff=diff, kind=kind)
        )

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
