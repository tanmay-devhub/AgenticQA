"""One-shot repair for a broken generated pytest module.

If the tests we generated fail to import or collect (syntax error, missing
symbol, stray backtick from the model), pytest returns non-zero before it can
run a single case -- and mutmut then can't score anything. We give the model
exactly ONE chance to fix its own output by feeding back the failing source
plus the pytest stderr. If that still fails, the round aborts.

Kept small on purpose: no round-tripping, no history. If a single repair round
doesn't fix it, the model's output isn't salvageable and we should surface the
failure instead of burning more tokens.
"""

from __future__ import annotations

from pathlib import Path

from mutagen.agent.llm import LLM
from mutagen.testgen.tier1 import _strip_fences

SYSTEM = (
    "You are a senior Python engineer fixing a broken pytest module. The "
    "module below fails to run under pytest. Analyze the error, then return "
    "the ENTIRE fixed module source. Rules: import target as "
    "`from target import ...`, standard library + pytest only, assert only "
    "on exception TYPE (never on message text). Return ONLY the Python "
    "source -- no prose, no markdown fences."
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

Return the ENTIRE fixed test module source now.
"""


def repair(llm: LLM, *, target_source: Path, tests_path: Path, pytest_stderr: str) -> str:
    source = target_source.read_text(encoding="utf-8")
    tests = tests_path.read_text(encoding="utf-8")
    resp = llm.complete(
        "codegen",
        system=SYSTEM,
        user=USER_TEMPLATE.format(source=source, tests=tests, stderr=pytest_stderr[-4000:]),
    )
    return _strip_fences(resp.text)
