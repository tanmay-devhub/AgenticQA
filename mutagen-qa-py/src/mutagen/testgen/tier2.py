"""Tier 2: boundary / error-path / negative tests driven by planner specs.

Same shape as ``tier1.generate``: one LLM call, returns a runnable pytest
module string. The prompt is round-N-aware -- it references the specific
survivors the planner picked and the technique hint, so the model targets the
gaps that still exist rather than re-covering happy paths.
"""

from __future__ import annotations

from pathlib import Path

from mutagen.agent.llm import LLM
from mutagen.agent.planner import TestSpec
from mutagen.testgen.tier1 import _focus_directive, _read_focus, _strip_fences

SYSTEM = (
    "You are a senior Python QA engineer writing a follow-up pytest module. A "
    "previous round of tests has already covered the happy paths. Your job is "
    "to KILL the specific mutations that survived. Prefer "
    "@pytest.mark.parametrize; keep the module lean; assert on exception TYPE "
    "only (never on message text). Return ONLY valid Python source for a "
    "single test module -- no commentary, no markdown fences. Import via "
    "`from target import <symbols>`. Standard library and pytest only."
)


def _trim_diff_headers(diff: str) -> str:
    """Strip ``---`` / ``+++`` file lines and ``@@`` hunk markers.

    Every mutant in a cluster shares the same file header; repeating them
    per-mutant burns prompt tokens without adding information the codegen
    couldn't infer from the ``file=`` in the spec block above.
    """
    return "\n".join(
        ln for ln in (diff or "").splitlines()
        if not ln.startswith(("--- ", "+++ ", "@@"))
    ).strip()


def _render_specs(specs: list[TestSpec]) -> str:
    blocks: list[str] = []
    for i, spec in enumerate(specs, 1):
        surv_lines = []
        for m in spec.survivors:
            loc = f"line {m.line}" if m.line is not None else "line ?"
            diff = _trim_diff_headers(m.diff or "")
            surv_lines.append(f"    - id={m.id} ({loc}, kind={m.kind}):\n```diff\n{diff}\n```")
        surv_block = "\n".join(surv_lines) if surv_lines else "    (no diffs available)"
        cov_hint = (
            f"\n  Coverage: lines {spec.uncovered_lines} in this function are NOT "
            f"executed by any test yet -- prioritize inputs that exercise them."
            if spec.uncovered_lines
            else ""
        )
        blocks.append(
            f"Spec {i}: function `{spec.function or '?'}` in `{spec.file}`\n"
            f"  Dominant mutation kind: {spec.dominant_kind}\n"
            f"  Suggested technique: {spec.technique_hint}{cov_hint}\n"
            f"  Surviving mutations to kill:\n{surv_block}"
        )
    return "\n\n".join(blocks)


USER_TEMPLATE = """Target module source (file: target.py):

```python
{source}
```

The following mutations survived the previous round of tests. Write a pytest
module `test_round_N.py` that kills them. For each spec, add the smallest set
of parametrized tests that make the surviving mutation observable in the
return value or the raised exception TYPE.

{specs}

Rules:
- Do NOT re-test happy paths already covered elsewhere; focus on the specs above.
- Use `pytest.raises` for error paths; assert only the exception type, not `match=`.
- Prefer one parametrized test per spec over many single-case tests.
- Keep total test count under 15.
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
