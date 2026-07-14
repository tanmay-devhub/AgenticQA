"""Two-shot repair for a broken generated pytest module.

If the tests we generated fail to import or collect (syntax error, missing
symbol, stray backtick from the model), pytest returns non-zero before it can
run a single case -- and mutmut then can't score anything. We give the model
up to TWO chances to fix its own output:

    attempt 0: same temperature as codegen. Usually catches syntax + import
               bugs, occasional off-by-one in assertions.
    attempt 1: escalates the codegen role's temperature by +0.3 and re-frames
               the prompt as "your fix still broke; try a different tactic."
               This exists because we saw qwen3-coder loop on the same wrong
               CSV expectation twice; a temperature bump breaks that loop.

Kept to two shots on purpose: if both attempts fail the model's mental model
of the target is genuinely off, and we should surface that instead of burning
tokens indefinitely.
"""

from __future__ import annotations

from pathlib import Path

from mutagen.agent.llm import LLM
from mutagen.testgen.tier1 import _strip_fences

MAX_REPAIR_ATTEMPTS = 2
_TEMP_BUMP_PER_ATTEMPT = 0.3

SYSTEM = (
    "You are a senior Python engineer fixing a broken pytest module. The "
    "module below fails to run under pytest. Analyze the error, then return "
    "the ENTIRE fixed module source. Rules: import target as "
    "`from target import ...`, standard library + pytest only, assert only "
    "on exception TYPE (never on message text). Return ONLY the Python "
    "source -- no prose, no markdown fences."
)

_ATTEMPT1_HINT = (
    "\n\nNOTE: an earlier repair attempt still failed pytest. Do NOT reuse the "
    "same expected values -- re-read the target docstring carefully and revise "
    "your assertions against what it actually documents, then return the whole "
    "fixed module."
)

USER_TEMPLATE = """Target module (target.py):

```python
{source}
```

Current broken test module:

```python
{tests}
```

pytest output (stderr):

```
{stderr}
```

Return the ENTIRE fixed test module source now.{hint}"""


def repair(
    llm: LLM,
    *,
    target_source: Path,
    tests_path: Path,
    pytest_stderr: str,
    attempt: int = 0,
) -> str:
    """Return a repaired test-module source string.

    ``attempt`` is 0-indexed. Attempt 1 escalates temperature and appends a
    hint telling the model that its previous fix still failed, so it should
    revisit its assumptions instead of writing the same wrong tests again.
    """
    source = target_source.read_text(encoding="utf-8")
    tests = tests_path.read_text(encoding="utf-8")
    hint = _ATTEMPT1_HINT if attempt >= 1 else ""

    # Best-effort temperature bump on the codegen role. Restored via
    # try/finally so a caller re-using the LLM instance sees the config
    # unchanged. FakeLLM (used in tests) has no ``_cfg`` -- skip cleanly.
    role = getattr(getattr(llm, "_cfg", None), "llm", None)
    role = getattr(role, "codegen", None) if role else None
    original_temp = getattr(role, "temperature", None)
    if role is not None and attempt >= 1 and original_temp is not None:
        role.temperature = min(1.5, original_temp + _TEMP_BUMP_PER_ATTEMPT * attempt)
    try:
        resp = llm.complete(
            "codegen",
            system=SYSTEM,
            user=USER_TEMPLATE.format(
                source=source, tests=tests, stderr=pytest_stderr[-4000:], hint=hint,
            ),
        )
    finally:
        if role is not None and original_temp is not None:
            role.temperature = original_temp
    return _strip_fences(resp.text)
