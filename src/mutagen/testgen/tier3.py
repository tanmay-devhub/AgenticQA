"""Tier 3: property-based tests via Hypothesis.

Escalation target for the loop: reached after Tier-2 example-based tests
plateau. Property tests state invariants (round-trips, bounds, ordering) that
Hypothesis probes with a diverse set of inputs, which is often the only way to
kill arithmetic-and-return-value survivors that a hand-crafted case list keeps
missing.

Same shape as tier1 / tier2: single LLM call, returns a runnable pytest module
string. The prompt is grounded in the surviving mutations so Hypothesis
strategies are chosen to make those specific mutations fail on at least one
generated example.
"""

from __future__ import annotations

from pathlib import Path

from mutagen.agent.llm import LLM
from mutagen.agent.planner import TestSpec
from mutagen.testgen.tier1 import _focus_directive, _read_focus, _strip_fences
from mutagen.testgen.tier2 import _render_specs

SYSTEM = (
    "You are a senior Python QA engineer writing PROPERTY-BASED tests with "
    "Hypothesis. Previous rounds of example-based tests have plateaued: some "
    "mutations still survive. Your job is to write invariants that Hypothesis "
    "can falsify on at least one generated input, using `@given` and "
    "appropriate `strategies`. Rules: import target as "
    "`from target import ...`; import strategies as `from hypothesis import "
    "given, strategies as st`; assert only on return values or exception "
    "TYPE (never on message text); keep it lean (<= 8 property tests). "
    "Return ONLY the Python source, no prose, no markdown fences."
)

USER_TEMPLATE = """Target module source (file: target.py):

```python
{source}
```

Example-based tests have plateaued. The following mutations still survive.
Write a Hypothesis-driven pytest module `test_round_N.py` whose properties
would be falsified by at least one of these mutations.

{specs}

Rules:
- Pick strategies (`st.integers`, `st.text`, `st.lists`, etc.) whose range
  actually exercises the surviving mutation. Constrain with `min_value`,
  `max_value`, `alphabet`, etc. so tests don't drift into undefined behavior.
- Prefer invariants that would OBSERVABLY differ under the mutation:
  round-trips, bounds, monotonicity, algebraic identities.
- Use `@pytest.mark.parametrize` alongside `@given` when useful.
- Keep total test count under 10.
- Return ONLY the Python source, nothing else.
"""


def generate(llm: LLM, *, target_source: Path, specs: list[TestSpec]) -> str:
    source = target_source.read_text(encoding="utf-8")
    focus = _read_focus(target_source.parent)
    user = _focus_directive(focus) + USER_TEMPLATE.format(
        source=source, specs=_render_specs(specs),
    )
    resp = llm.complete("codegen", system=SYSTEM, user=user)
    return _strip_fences(resp.text)
