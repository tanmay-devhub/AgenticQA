"""Planner v0: rule-based survivor -> TestSpec.

Consumes the classifier's verdicts, drops non-``real_gap`` survivors, then
clusters remaining survivors by (file, function) and maps each cluster's
dominant mutation kind to a coarse technique. The technique becomes a hint
appended to the T2 generator prompt; the generator still writes the actual
tests.

Kept deliberately dumb. Anything smarter (coverage-guided, per-mutant reasoning)
belongs behind an LLM-based planner v1 in Phase 3.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.agent.classifier import ClassifiedSurvivor
from mutagen.mutation.report import Mutant, MutationKind

# Loaded from the cross-language schema so Python and JS share the exact same
# technique text. Path walks up to <repo>/shared/schema/.
# parents[0]=agent, [1]=mutagen, [2]=src, [3]=mutagen-qa-py, [4]=repo root.
_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "shared" / "schema" / "technique_by_kind.json"
_raw_schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_TECHNIQUE_BY_KIND: dict[MutationKind, str] = {
    k: v for k, v in _raw_schema.items() if not k.startswith("$")
}


@dataclass
class TestSpec:
    """One planner directive for the T2 generator.

    Groups survivors that live in the same function so the generator can write
    a small number of parametrized tests instead of one test per mutant.
    """
    # pytest tries to collect any class whose name starts with "Test" -- opt out.
    __test__ = False

    file: str
    function: str | None
    dominant_kind: MutationKind
    technique_hint: str
    survivors: list[Mutant] = field(default_factory=list)
    uncovered_lines: list[int] = field(default_factory=list)


_FUNCTION_FROM_ID_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)__mutmut_\d+$")


def _function_from_id(mutant_id: str) -> str | None:
    """Extract function name from mutmut IDs like ``target.parse_range__mutmut_3``."""
    tail = mutant_id.rsplit(".", 1)[-1]
    m = _FUNCTION_FROM_ID_RE.match(tail)
    return m.group(1) if m else None


def plan_specs(
    classified: list[ClassifiedSurvivor],
    *,
    missing_lines: list[int] | None = None,
) -> list[TestSpec]:
    """Cluster real_gap survivors by (file, function); return one spec per cluster.

    If ``missing_lines`` is provided (from coverage), each cluster gets the subset
    of uncovered lines that fall within its span of surviving mutations. This is a
    hint for the T2 generator, not a hard requirement.
    """
    real = [c for c in classified if c.verdict == "real_gap"]
    if not real:
        return []

    clusters: dict[tuple[str, str | None], list[Mutant]] = defaultdict(list)
    for c in real:
        m = c.mutant
        key = (m.file or "target.py", _function_from_id(m.id))
        clusters[key].append(m)

    specs: list[TestSpec] = []
    for (file, function), mutants in clusters.items():
        kind_counter: Counter[MutationKind] = Counter(m.kind for m in mutants)
        dominant = kind_counter.most_common(1)[0][0]
        uncovered = _lines_in_cluster_span(mutants, missing_lines or [])
        specs.append(
            TestSpec(
                file=file,
                function=function,
                dominant_kind=dominant,
                technique_hint=_TECHNIQUE_BY_KIND[dominant],
                survivors=mutants,
                uncovered_lines=uncovered,
            )
        )
    return specs


def _lines_in_cluster_span(mutants: list[Mutant], missing: list[int]) -> list[int]:
    """Return uncovered lines whose numbers fall inside [min, max] of the cluster's
    surviving-mutation lines (with a small pad). Coarse but avoids leaking noise
    from other functions in the same file."""
    lines = [m.line for m in mutants if m.line is not None]
    if not lines or not missing:
        return []
    lo, hi = min(lines) - 2, max(lines) + 8  # heuristic function-span buffer
    return sorted({ln for ln in missing if lo <= ln <= hi})
