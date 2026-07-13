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

# Matches any `def test_<name>` (optionally async, any indentation), at a line
# start. Class-based tests are rare in generated output, so we don't chase them.
_TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+test_\w+", re.MULTILINE)


def _strip_fences(text: str) -> str:
    """Some models still wrap output in ```python … ``` despite instructions."""
    stripped = text.strip()
    m = _FENCE_RE.match(stripped)
    if m:
        return m.group(1).strip() + "\n"
    return stripped + ("\n" if not stripped.endswith("\n") else "")


def has_tests(source: str) -> bool:
    """True iff ``source`` contains at least one ``def test_*`` definition.

    Used by the loop to short-circuit a round when codegen produced no tests
    (typical cause: a reasoning-heavy model spent its whole ``max_tokens``
    budget on hidden `<think>` output and never emitted code). Without this
    check, mutmut would silently rerun against the previous round's tests and
    the round would look "successful" while doing zero real work.
    """
    return bool(_TEST_DEF_RE.search(source))


# --- optional user focus -------------------------------------------------

# When the user provided a plain-English focus at job submit time, jobs.py
# writes it to ``<workdir>/focus.txt``. Reading it here (instead of threading
# a parameter down from the loop) keeps every codegen call-site the same
# regardless of whether focus was set.

_FOCUS_FILENAME = "focus.txt"


def _read_focus(workdir: Path) -> str | None:
    f = workdir / _FOCUS_FILENAME
    if not f.is_file():
        return None
    text = f.read_text(encoding="utf-8", errors="replace").strip()
    return text or None


def _focus_directive(focus: str | None) -> str:
    """Prompt fragment inserted at the top of the user message when focus is set.

    Empty string when no focus, so unfocused runs keep the exact prior prompt
    behavior and stay reproducible against prior benchmarks.
    """
    if not focus:
        return ""
    return (
        "TESTING FOCUS (user priority):\n"
        f"{focus}\n\n"
        "Weight your test cases toward this concern first. If the focus is "
        "narrow (a specific function, edge case, or code path), you may still "
        "add a couple of sanity tests for the rest, but the MAJORITY of the "
        "module should target the focus above.\n\n"
    )


def generate(llm: LLM, *, target_source: Path) -> str:
    source = target_source.read_text(encoding="utf-8")
    focus = _read_focus(target_source.parent)
    user = _focus_directive(focus) + USER_TEMPLATE.format(source=source)
    resp = llm.complete("codegen", system=SYSTEM, user=user)
    return _strip_fences(resp.text)
