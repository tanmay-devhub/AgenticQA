"""Typed report structures for a mutmut run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MutantStatus = Literal["killed", "survived", "timeout", "suspicious", "skipped", "unknown"]


@dataclass
class Mutant:
    id: str                 # mutmut's identifier (e.g. "target.parse_range__mutmut_3")
    file: str | None
    line: int | None
    status: MutantStatus
    diff: str | None = None  # unified diff of the mutation, if we collected it


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
