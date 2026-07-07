"""Tier 1: happy-path + example-based + parameterized tests.

Prompts the codegen role with target source + docstring and returns a
runnable pytest module string. Phase 1 uses one shot; no iterative refine.
"""

from __future__ import annotations

import re
from pathlib import Path

from mutagen.agent.llm import LLM

SYSTEM = (
    "You are a senior Python QA engineer. Write tight, high-signal pytest "
    "tests. Prefer @pytest.mark.parametrize over duplicate test bodies. "
    "Cover happy paths, boundary cases, and error paths. NO commentary, "
    "NO markdown fences: return ONLY valid Python source for a single test "
    "module. Import the target as: `from target import <symbols>`. "
    "Do not import anything not in the standard library or pytest."
)

USER_TEMPLATE = """Target module source (file: target.py):

```python
{source}
```

Write a pytest module `test_generated.py` that:
- imports the public symbols from `target`,
- uses `@pytest.mark.parametrize` for the happy-path and boundary cases,
- has separate tests for each documented error path using `pytest.raises`,
- when using `pytest.raises`, assert ONLY the exception TYPE. Do NOT pass
  `match=` — error message wording is an implementation detail and brittle,
- keeps the total test count lean (< 20 tests) but bug-catching,
- has NO comments explaining what code does; only docstrings on tests
  when the intent isn't obvious.

Return ONLY the Python source, nothing else.
"""


_FENCE_RE = re.compile(r"^```(?:python)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_fences(text: str) -> str:
    """Some models still wrap output in ```python … ``` despite instructions."""
    stripped = text.strip()
    m = _FENCE_RE.match(stripped)
    if m:
        return m.group(1).strip() + "\n"
    return stripped + ("\n" if not stripped.endswith("\n") else "")


def generate(llm: LLM, *, target_source: Path) -> str:
    source = target_source.read_text(encoding="utf-8")
    resp = llm.complete(
        "codegen",
        system=SYSTEM,
        user=USER_TEMPLATE.format(source=source),
    )
    return _strip_fences(resp.text)
