"""Typed report structures for a mutmut run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MutantStatus = Literal["killed", "survived", "timeout", "suspicious", "skipped", "unknown"]

# Coarse mutation categories inferred from the unified diff. The planner maps
# these to test techniques -- e.g. `comparison` -> boundary tests, `arithmetic`
# / `constant` -> off-by-one / value substitution, `return` -> equality on
# specific inputs. `other` is the residual bucket.
MutationKind = Literal[
    "comparison",   # <, <=, >, >=, ==, !=, is, is not, in, not in
    "arithmetic",   # + - * / // % **
    "constant",     # numeric / bool literal changed
    "return",       # return X -> return None / different value
    "boolean",      # and/or/not swaps
    "keyword",      # break/continue/pass/raise swaps
    "call",         # function-call arg or callee change
    "other",
]


@dataclass
class Mutant:
    id: str                 # mutmut's identifier (e.g. "target.parse_range__mutmut_3")
    file: str | None
    line: int | None
    status: MutantStatus
    diff: str | None = None       # unified diff of the mutation, if we collected it
    kind: MutationKind = "other"  # coarse category inferred from the diff

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file": self.file,
            "line": self.line,
            "status": self.status,
            "kind": self.kind,
            "diff": self.diff,
        }


@dataclass
class MutationReport:
    total: int
    killed: int
    survived: int
    timeout: int = 0
    suspicious: int = 0
    skipped: int = 0
    survivors: list[Mutant] = field(default_factory=list)
    disabled_types: list[str] = field(default_factory=list)

    @property
    def kill_rate(self) -> float:
        denom = self.killed + self.survived + self.timeout + self.suspicious
        if denom == 0:
            return 0.0
        return self.killed / denom

    def format_summary(self) -> str:
        pct = self.kill_rate * 100
        base = (
            f"killed={self.killed}/{self.total}  "
            f"survived={self.survived}  "
            f"timeout={self.timeout}  "
            f"suspicious={self.suspicious}  "
            f"kill_rate={pct:.1f}%"
        )
        if self.disabled_types:
            base += f"\nfiltered mutation types: {','.join(self.disabled_types)}"
        return base

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "killed": self.killed,
            "survived": self.survived,
            "timeout": self.timeout,
            "suspicious": self.suspicious,
            "skipped": self.skipped,
            "kill_rate": self.kill_rate,
            "disabled_types": list(self.disabled_types),
            "survivors": [m.to_dict() for m in self.survivors],
        }
