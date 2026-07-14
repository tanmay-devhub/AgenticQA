"""LLM-driven analysis of a completed mutation run.

For each survivor the analysis LLM returns a strict JSON blob describing
*why* the test suite missed the mutation and how serious that gap is. A
final aggregation call produces a prose vulnerability summary + a
prioritized action list.

The output is a plain-dataclass ``AnalysisReport`` that serializes to
``workdir/analysis.json``. The web layer renders both HTML and PDF views
from that file, so an analysis is portable across processes.

The design keeps LLM calls small and independent: one call per survivor,
one final aggregation call. If the analysis LLM chokes on one survivor
(rate limit, malformed JSON, whatever) we degrade to an "unknown"
verdict for that entry instead of losing the whole report.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from mutagen.agent.llm import LLM
from mutagen.mutation.report import Mutant, MutationReport

Severity = Literal["critical", "high", "low", "unknown"]
Category = Literal["equivalent", "test_gap", "spec_gap", "low_value", "unknown"]

_SEVERITY_ORDER = {"critical": 0, "high": 1, "low": 2, "unknown": 3}
_VALID_SEVERITIES: frozenset[str] = frozenset(("critical", "high", "low", "unknown"))
_VALID_CATEGORIES: frozenset[str] = frozenset(
    ("equivalent", "test_gap", "spec_gap", "low_value", "unknown")
)

# Cap what we send the model. Real targets can be large; sending everything
# per survivor wastes tokens and can blow past cloud context limits.
_MAX_TARGET_CHARS = 4000
_MAX_TESTS_CHARS = 3000
_MAX_DIFF_CHARS = 1500


@dataclass
class SurvivorAnalysis:
    mutant_id: str
    file: str | None
    line: int | None
    kind: str
    diff: str | None
    root_cause: str
    category: Category
    severity: Severity
    suggested_test: str
    fix_hint: str

    def to_dict(self) -> dict:
        return {
            "mutant_id": self.mutant_id,
            "file": self.file,
            "line": self.line,
            "kind": self.kind,
            "diff": self.diff,
            "root_cause": self.root_cause,
            "category": self.category,
            "severity": self.severity,
            "suggested_test": self.suggested_test,
            "fix_hint": self.fix_hint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SurvivorAnalysis:
        return cls(
            mutant_id=d["mutant_id"],
            file=d.get("file"),
            line=d.get("line"),
            kind=d.get("kind", "other"),
            diff=d.get("diff"),
            root_cause=d.get("root_cause", ""),
            category=d.get("category", "unknown"),
            severity=d.get("severity", "unknown"),
            suggested_test=d.get("suggested_test", ""),
            fix_hint=d.get("fix_hint", ""),
        )


@dataclass
class AnalysisReport:
    workdir_name: str
    target_name: str
    generated_at: float           # unix ts
    model: str                    # analysis model used
    # Raw run metrics carried through so the report page is self-contained.
    total_mutants: int
    killed: int
    survived: int
    timeout: int
    kill_rate: float
    # LLM-produced content.
    survivors: list[SurvivorAnalysis] = field(default_factory=list)
    verdict: str = ""             # prose overall summary
    action_items: list[str] = field(default_factory=list)
    # User-supplied testing focus (echoed into the report so readers see the
    # context the LLM was judging severity against). None for general runs.
    focus: str | None = None

    def severity_counts(self) -> dict[str, int]:
        counts = {"critical": 0, "high": 0, "low": 0, "unknown": 0}
        for s in self.survivors:
            counts[s.severity] = counts.get(s.severity, 0) + 1
        return counts

    def category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self.survivors:
            counts[s.category] = counts.get(s.category, 0) + 1
        return counts

    def sorted_survivors(self) -> list[SurvivorAnalysis]:
        return sorted(self.survivors, key=lambda s: _SEVERITY_ORDER.get(s.severity, 9))

    def to_dict(self) -> dict:
        return {
            "workdir_name": self.workdir_name,
            "target_name": self.target_name,
            "generated_at": self.generated_at,
            "model": self.model,
            "total_mutants": self.total_mutants,
            "killed": self.killed,
            "survived": self.survived,
            "timeout": self.timeout,
            "kill_rate": self.kill_rate,
            "survivors": [s.to_dict() for s in self.survivors],
            "verdict": self.verdict,
            "action_items": list(self.action_items),
            "focus": self.focus,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AnalysisReport:
        return cls(
            workdir_name=d["workdir_name"],
            target_name=d.get("target_name", ""),
            generated_at=d.get("generated_at", 0.0),
            model=d.get("model", ""),
            total_mutants=d.get("total_mutants", 0),
            killed=d.get("killed", 0),
            survived=d.get("survived", 0),
            timeout=d.get("timeout", 0),
            kill_rate=d.get("kill_rate", 0.0),
            survivors=[SurvivorAnalysis.from_dict(s) for s in d.get("survivors", [])],
            verdict=d.get("verdict", ""),
            action_items=list(d.get("action_items", [])),
            focus=d.get("focus"),
        )


# --- LLM plumbing --------------------------------------------------------

_SURVIVOR_SYSTEM = (
    "You are a mutation-testing analyst. A test suite failed to detect a "
    "code mutation (a 'survivor'). Your job: judge WHY the tests missed it, "
    "how serious the gap is, and what one test would catch it.\n\n"
    "Output STRICT JSON with these fields (no markdown, no prose outside JSON):\n"
    "{\n"
    '  "root_cause": "one sentence, why the tests missed this mutation",\n'
    '  "category": one of "equivalent" | "test_gap" | "spec_gap" | "low_value",\n'
    '  "severity": one of "critical" | "high" | "low",\n'
    '  "suggested_test": "a concrete pytest snippet (may include imports) that would kill this mutant",\n'
    '  "fix_hint": "one-line advice for the developer"\n'
    "}\n\n"
    "Category guide:\n"
    "  equivalent  -- the mutant produces the same behavior as the original; not a real gap.\n"
    "  test_gap    -- tests could catch it but no assertion covers the affected input/output.\n"
    "  spec_gap    -- the intended behavior for the affected path is ambiguous; needs a decision first.\n"
    "  low_value   -- a real gap but on trivial/error-message code where the risk is low.\n\n"
    "Severity guide:\n"
    "  critical -- a wrong result could cause data loss, silent corruption, security bypass, or "
    "wrong business decisions.\n"
    "  high     -- wrong behavior on realistic inputs but the failure is loud or recoverable.\n"
    "  low      -- unlikely to bite in practice, or purely cosmetic."
)

_VERDICT_SYSTEM = (
    "You are the same mutation-testing analyst. You've already judged each survivor. "
    "Now write a short prose summary and a prioritized action list for the developer. "
    "Output STRICT JSON:\n"
    "{\n"
    '  "verdict": "2-4 sentences summarizing how well-tested this code is and what class of gaps remain",\n'
    '  "action_items": ["short imperative sentence", ...]  // 3-6 items, most important first\n'
    "}\n"
    "Do NOT include markdown or any text outside the JSON object."
)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} chars omitted]"


def _extract_json(raw: str) -> dict:
    """Pull the first {...} block from an LLM response and parse it.

    Models sometimes wrap the JSON in ```json fences or add a leading sentence
    even when instructed not to; the regex tolerates both. Raises ValueError
    on unparseable output so the caller can degrade gracefully.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        # ```json\n{...}\n``` or ```\n{...}\n```
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    match = _JSON_OBJECT_RE.search(raw)
    if not match:
        raise ValueError(f"no JSON object found in LLM response: {raw[:200]!r}")
    return json.loads(match.group(0))


def _coerce_str(value: object, *, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _coerce_enum(value: object, allowed: frozenset[str], *, default: str) -> str:
    v = _coerce_str(value).lower()
    return v if v in allowed else default


def _fallback_analysis(m: Mutant, reason: str) -> SurvivorAnalysis:
    """Return a placeholder analysis when the LLM call fails for this survivor.

    We keep it visible (severity=unknown, category=unknown) rather than hiding
    it -- the human reading the report should see that the analyst couldn't
    reach a conclusion here.
    """
    return SurvivorAnalysis(
        mutant_id=m.id,
        file=m.file,
        line=m.line,
        kind=m.kind,
        diff=m.diff,
        root_cause=f"analysis unavailable ({reason})",
        category="unknown",
        severity="unknown",
        suggested_test="",
        fix_hint="re-run report generation once the analysis LLM is reachable",
    )


def _focus_block(focus: str | None) -> str:
    """Prompt fragment reminding the analyst of the user's stated priority.

    A survivor OUTSIDE the focus area should generally read as lower severity
    than one inside it, since the user explicitly cared about the focus.
    """
    if not focus:
        return ""
    return (
        "USER TESTING FOCUS (what they explicitly asked to be tested):\n"
        f"\"{focus}\"\n"
        "When rating severity, weigh survivors that fall inside this focus "
        "area more heavily than those outside it.\n\n"
    )


def _analyze_survivor(
    llm: LLM, m: Mutant, target_src: str, tests_src: str, focus: str | None,
) -> SurvivorAnalysis:
    user = (
        f"{_focus_block(focus)}"
        f"TARGET SOURCE:\n```python\n{_clip(target_src, _MAX_TARGET_CHARS)}\n```\n\n"
        f"MUTATION DIFF (this change was NOT detected by the test suite):\n"
        f"```diff\n{_clip(m.diff or '(no diff captured)', _MAX_DIFF_CHARS)}\n```\n\n"
        f"CURRENT TESTS:\n```python\n{_clip(tests_src, _MAX_TESTS_CHARS)}\n```\n\n"
        f"Mutation kind (rough classification): {m.kind}\n"
        f"File / line: {m.file}:{m.line}\n\n"
        "Respond with the JSON object described in the system prompt."
    )
    try:
        resp = llm.complete("analysis", system=_SURVIVOR_SYSTEM, user=user)
        obj = _extract_json(resp.text)
    except Exception as e:  # noqa: BLE001 -- degrade per-survivor, not whole report
        return _fallback_analysis(m, f"{type(e).__name__}: {e}"[:200])

    return SurvivorAnalysis(
        mutant_id=m.id,
        file=m.file,
        line=m.line,
        kind=m.kind,
        diff=m.diff,
        root_cause=_coerce_str(obj.get("root_cause"), default="(no explanation)"),
        category=_coerce_enum(obj.get("category"), _VALID_CATEGORIES, default="unknown"),
        severity=_coerce_enum(obj.get("severity"), _VALID_SEVERITIES, default="unknown"),
        suggested_test=_coerce_str(obj.get("suggested_test")),
        fix_hint=_coerce_str(obj.get("fix_hint")),
    )


def _summarize(llm: LLM, target_name: str, report: MutationReport,
               analyses: list[SurvivorAnalysis], focus: str | None) -> tuple[str, list[str]]:
    """Ask the LLM for an overall verdict + action list.

    Returns ``("", [])`` on failure -- the report is still useful without a
    verdict, so we don't crash the whole pipeline over one call.
    """
    if not analyses:
        return (
            "All mutants were killed. The test suite covers every mutation the "
            "runner produced for this target.",
            [],
        )
    survivor_lines = []
    for a in analyses:
        loc = f"{a.file}:{a.line}" if a.file else a.mutant_id
        survivor_lines.append(
            f"- [{a.severity}/{a.category}] {loc} ({a.kind}): {a.root_cause}"
        )
    user = (
        f"{_focus_block(focus)}"
        f"Target: {target_name}\n"
        f"Kill rate: {report.kill_rate*100:.1f}%  "
        f"(killed={report.killed}, survived={report.survived}, timeout={report.timeout})\n\n"
        "Survivor findings:\n" + "\n".join(survivor_lines) + "\n\n"
        "Respond with the JSON object described in the system prompt."
    )
    try:
        resp = llm.complete("analysis", system=_VERDICT_SYSTEM, user=user)
        obj = _extract_json(resp.text)
    except Exception:  # noqa: BLE001 -- optional aggregation, degrade quietly
        return "", []
    verdict = _coerce_str(obj.get("verdict"))
    raw_actions = obj.get("action_items") or []
    actions: list[str] = []
    if isinstance(raw_actions, list):
        for a in raw_actions:
            s = _coerce_str(a)
            if s:
                actions.append(s)
    return verdict, actions


def _find_target_source(workdir: Path) -> str:
    """target.py is the copy the loop mutates against; falls back to _input.py."""
    for name in ("target.py", "_input.py"):
        p = workdir / name
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    return ""


def _find_latest_tests(workdir: Path) -> str:
    """Highest-numbered ``test_round_N.py`` -- that's the final generated suite."""
    candidates = sorted(workdir.glob("test_round_*.py"))
    if not candidates:
        return ""
    return candidates[-1].read_text(encoding="utf-8", errors="replace")


def _find_focus(workdir: Path) -> str | None:
    """Read the plain-English focus the user set at submit time, if any."""
    f = workdir / "focus.txt"
    if not f.is_file():
        return None
    text = f.read_text(encoding="utf-8", errors="replace").strip()
    return text or None


def analyze_run(
    workdir: Path,
    *,
    llm: LLM,
    report: MutationReport,
    target_name: str,
    model_name: str,
) -> AnalysisReport:
    """Analyze every survivor + produce an overall verdict.

    Callers own the ``LLM`` instance (so we don't build a second one and lose
    the token accounting), and pass in the final ``MutationReport`` so this
    function doesn't have to guess which round is authoritative.
    """
    target_src = _find_target_source(workdir)
    tests_src = _find_latest_tests(workdir)
    focus = _find_focus(workdir)

    analyses: list[SurvivorAnalysis] = []
    for m in report.survivors:
        analyses.append(_analyze_survivor(llm, m, target_src, tests_src, focus))

    verdict, actions = _summarize(llm, target_name, report, analyses, focus)

    return AnalysisReport(
        workdir_name=workdir.name,
        target_name=target_name,
        generated_at=time.time(),
        model=model_name,
        total_mutants=report.total,
        killed=report.killed,
        survived=report.survived,
        timeout=report.timeout,
        kill_rate=report.kill_rate,
        survivors=analyses,
        verdict=verdict,
        action_items=actions,
        focus=focus,
    )
