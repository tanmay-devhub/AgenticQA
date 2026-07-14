"""Offline helpers for exercising the loop without hitting real LLM APIs.

Useful for:
    - Unit / integration tests of the loop, planner, and classifier.
    - Local demos that don't want to burn tokens.
    - CI runs that must stay hermetic.

`FakeLLM` mimics the `LLM.complete` interface. Callers supply a mapping of role
-> list of canned response texts, consumed FIFO. Each call is recorded, so tests
can assert what was asked for. It intentionally does NOT try to be smart about
routing by prompt content -- tests should be explicit about the order of
responses they want.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mutagen.agent.llm import LLMResponse, Role, Usage


@dataclass
class RecordedCall:
    role: Role
    system: str
    user: str


@dataclass
class FakeLLM:
    """Drop-in replacement for `LLM` that returns canned responses per role."""

    responses: dict[Role, list[str]] = field(default_factory=dict)
    calls: list[RecordedCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    def complete(self, role: Role, *, system: str, user: str) -> LLMResponse:
        self.calls.append(RecordedCall(role=role, system=system, user=user))
        queue = self.responses.get(role)
        if not queue:
            raise RuntimeError(f"FakeLLM has no more canned {role!r} responses")
        text = queue.pop(0)
        out = LLMResponse(
            text=text,
            prompt_tokens=len(user) // 4,
            completion_tokens=len(text) // 4,
            model=f"fake/{role}",
        )
        self.usage.record(role, out)
        return out
