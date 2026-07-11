"""Per-round debrief writer.

Emits a Markdown file per round (``<workdir>/round_N_debrief.md``) that
narrates the round in human-readable prose:

    - Which tests failed on the first pytest run (if any).
    - Whether repair was needed and whether it succeeded.
    - Which mutations survived, with diffs.
    - How the NEXT round classified those survivors and what technique it
      chose to attack them.

The debrief file is written in two passes:

    1. Right after a round finishes, ``write_round_body`` writes the pytest /
       repair / mutmut sections.
    2. When the loop begins the next round, ``append_handoff`` adds the
       classifier verdicts + planner specs so the file tells the full story
       "these mutants survived -> we classified them like this -> next round
       will use this technique."

This layout means the last round's debrief has NO handoff section (there is
no next round), which is intentional -- the loop's stop reason is written
into the run.json alongside.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from mutagen.agent.classifier import ClassifiedSurvivor
from mutagen.agent.planner import TestSpec

if TYPE_CHECKING:
    # Only referenced in a type hint; runtime access is duck-typed. Importing
    # for real would circularly re-import ``loop`` while ``loop`` is still
    # importing this module.
    from mutagen.agent.loop import RoundResult

# `FAILED tests/test_x.py::test_a - AssertionError: assert 1 == 2`
_FAILING_RE = re.compile(r"^FAILED\s+([^\s]+)(?:\s+-\s+(.*))?$", re.MULTILINE)
_MAX_FAILURES_LISTED = 20
_MAX_FAILURE_REASON_LEN = 200


def _parse_failing_tests(pytest_stdout: str) -> list[tuple[str, str]]:
    """Extract ``[(test_id, brief_reason), ...]`` from pytest's short-report.

    pytest's default output has a `short test summary info` block near the
    end with ``FAILED <path>::<name> - <reason>`` lines. We tolerate the
    reason being absent (older pytest, non-standard reporters).
    """
    matches = _FAILING_RE.findall(pytest_stdout or "")
    out: list[tuple[str, str]] = []
    for name, reason in matches[:_MAX_FAILURES_LISTED]:
        out.append((name, (reason or "").strip()[:_MAX_FAILURE_REASON_LEN]))
    return out


def _round_debrief_path(workdir: Path, index: int) -> Path:
    return workdir / f"round_{index}_debrief.md"


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:.1f}%" if x is not None else "-"


def write_round_body(workdir: Path, r: RoundResult) -> Path:
    """Write the pytest/repair/mutmut narrative for round ``r`` and return
    the file path. Overwrites any prior content."""
    lines: list[str] = []
    lines.append(f"# Round {r.index} — Tier {r.tier}")
    lines.append("")
    lines.append(f"- **elapsed:** {r.elapsed_s:.1f}s")
    lines.append(f"- **tests file:** `{r.tests_path.name}`")
    lines.append("")

    # -- pytest -------------------------------------------------------
    lines.append("## pytest")
    lines.append("")
    if r.initial_pytest_result is not None:
        initial_failures = _parse_failing_tests(r.initial_pytest_result.stdout)
        lines.append(
            f"Initial run: **FAILED** (rc={r.initial_pytest_result.returncode})."
        )
        if initial_failures:
            lines.append("")
            lines.append(f"First {len(initial_failures)} failing test(s):")
            lines.append("")
            for name, reason in initial_failures:
                if reason:
                    lines.append(f"- `{name}` — {reason}")
                else:
                    lines.append(f"- `{name}`")
        else:
            lines.append("")
            lines.append(
                "No `FAILED` markers were extractable from the pytest output "
                "-- collection failed before any test ran. Check "
                "`initial_pytest_result.stderr` for the traceback."
            )
    else:
        lines.append("Initial run: **passed** (no repair needed).")
    lines.append("")

    # -- repair -------------------------------------------------------
    lines.append("## repair")
    lines.append("")
    if r.repaired:
        outcome = "passed" if r.pytest_ok else "still failed"
        lines.append(
            f"Repair was invoked. Final pytest run: **{outcome}** "
            f"(rc={r.pytest_result.returncode})."
        )
        if not r.pytest_ok:
            final_failures = _parse_failing_tests(r.pytest_result.stdout)
            if final_failures:
                lines.append("")
                lines.append("Still failing after repair:")
                lines.append("")
                for name, reason in final_failures:
                    lines.append(f"- `{name}`" + (f" — {reason}" if reason else ""))
    else:
        lines.append("No repair attempted.")
    lines.append("")

    # -- mutmut -------------------------------------------------------
    lines.append("## mutmut")
    lines.append("")
    if r.report is None:
        lines.append(
            "Mutmut was **skipped** because pytest didn't reach a passing state."
        )
    else:
        rep = r.report
        lines.append(
            f"Killed **{rep.killed} / {rep.total}** "
            f"(kill rate {_fmt_pct(rep.kill_rate)}). "
            f"Survived: **{rep.survived}**."
        )
        if rep.survivors:
            lines.append("")
            lines.append("### surviving mutants")
            lines.append("")
            for m in rep.survivors:
                loc = f" (line {m.line})" if m.line is not None else ""
                lines.append(f"**`{m.id}`** — kind=`{m.kind}`{loc}")
                if m.diff:
                    lines.append("")
                    lines.append("```diff")
                    lines.append(m.diff.strip())
                    lines.append("```")
                lines.append("")

    # -- coverage (compact) -------------------------------------------
    if r.coverage is not None and r.coverage.missing_lines:
        lines.append("## coverage")
        lines.append("")
        lines.append(
            f"Uncovered lines in target.py: {r.coverage.missing_lines[:40]}"
            + (" …" if len(r.coverage.missing_lines) > 40 else "")
        )
        lines.append("")

    path = _round_debrief_path(workdir, r.index)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def append_handoff(
    workdir: Path,
    round_index: int,
    *,
    next_round_index: int,
    next_tier: int,
    classified: list[ClassifiedSurvivor],
    specs: list[TestSpec],
) -> Path:
    """Append the 'what round N+1 will do' section to round N's debrief.

    Called by the loop AFTER classify + plan for the next round has run, so
    we know exactly which survivors were flagged real_gap vs equivalent vs
    message_noise and which clusters the T2/T3 generator will target.
    """
    path = _round_debrief_path(workdir, round_index)

    verdicts: dict[str, int] = {"real_gap": 0, "equivalent": 0, "message_noise": 0}
    for c in classified:
        verdicts[c.verdict] = verdicts.get(c.verdict, 0) + 1

    lines: list[str] = ["", "## handoff to round " + str(next_round_index), ""]
    lines.append(f"Next tier: **T{next_tier}**.")
    lines.append("")
    lines.append("Classifier verdicts on this round's survivors:")
    lines.append("")
    lines.append(f"- `real_gap`: **{verdicts['real_gap']}** (fed to next generator)")
    lines.append(f"- `equivalent`: **{verdicts['equivalent']}** (skipped -- unkillable)")
    lines.append(f"- `message_noise`: **{verdicts['message_noise']}** (skipped -- non-behavioral)")
    lines.append("")

    if specs:
        lines.append(f"Round {next_round_index} will target {len(specs)} cluster(s):")
        lines.append("")
        for s in specs:
            fn = f"::{s.function}" if s.function else ""
            lines.append(
                f"- **`{s.file}{fn}`** — {len(s.survivors)} survivor(s), "
                f"dominant kind=`{s.dominant_kind}`."
            )
            lines.append(f"  - technique: {s.technique_hint}")
            if s.uncovered_lines:
                lines.append(f"  - uncovered lines in span: {s.uncovered_lines}")
        lines.append("")
    else:
        lines.append(
            "Planner produced no specs -- either no real_gap survivors "
            "remain or every cluster was empty after filtering. The loop "
            "will stop here."
        )
        lines.append("")

    # Append rather than rewrite so the body written earlier survives.
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
