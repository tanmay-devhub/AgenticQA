"""Survivor classifier.

Every unique survivor diff is fed to the planner-role LLM which tags it as:

    real_gap       -- a genuine untested behavior; feed to test generator.
    equivalent     -- semantically equivalent to the original; unkillable.
    message_noise  -- only affects an exception message / log string; not
                      behavioral. Skipped by the planner because our generator
                      is instructed not to assert on message text.

Results are cached by SHA-1 of the diff in ``<cache_dir>/classifier_cache.json``
so multi-round loops pay Gemini once per unique survivor across the whole run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mutagen.agent.llm import LLM
from mutagen.mutation.report import Mutant

Verdict = Literal["real_gap", "equivalent", "message_noise"]

SYSTEM = (
    "You are a mutation-testing analyst. You are shown the ORIGINAL source of "
    "a Python function and a UNIFIED DIFF of a single mutation applied to it. "
    "Decide, from behavior alone, whether the mutated code differs OBSERVABLY "
    "from the original for at least one legal input. Respond with a single "
    "line of JSON: "
    '{"verdict":"real_gap"|"equivalent"|"message_noise","reason":"<one short sentence>"}. '
    "Definitions: "
    "real_gap = there exists a legal input for which original and mutant produce "
    "different return values, raise different exception TYPES, or diverge in "
    "any externally visible way. "
    "equivalent = for every legal input both versions produce the same return "
    "value and the same exception type (message wording may differ). "
    "message_noise = the only difference is the wording of an exception message, "
    "log string, or f-string interpolated into a message. "
    "Output ONLY the JSON object; no prose, no code fences."
)

USER_TEMPLATE = """Original source (target.py):

```python
{source}
```

Mutation diff:

```diff
{diff}
```

Return the JSON verdict now."""


@dataclass
class ClassifiedSurvivor:
    mutant: Mutant
    verdict: Verdict
    reason: str


def _diff_hash(diff: str) -> str:
    return hashlib.sha1(diff.encode("utf-8", errors="replace")).hexdigest()


def _load_cache(cache_dir: Path) -> dict[str, dict]:
    p = cache_dir / "classifier_cache.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache_dir: Path, data: dict[str, dict]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "classifier_cache.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def _parse_verdict(text: str) -> tuple[Verdict, str]:
    """Extract the first JSON object from LLM output and validate it."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return "real_gap", "unparseable response; defaulted to real_gap"
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return "real_gap", "invalid JSON; defaulted to real_gap"
    v = obj.get("verdict")
    reason = str(obj.get("reason", ""))[:200]
    if v not in ("real_gap", "equivalent", "message_noise"):
        return "real_gap", f"unknown verdict {v!r}; defaulted to real_gap"
    return v, reason


def classify_survivors(
    llm: LLM,
    *,
    target_source: Path,
    survivors: list[Mutant],
    cache_dir: Path,
) -> list[ClassifiedSurvivor]:
    """Classify each survivor. Missing diffs default to ``real_gap`` (safe: at
    worst we ask the generator to attack a mutation it can't reason about)."""
    if not survivors:
        return []

    source = target_source.read_text(encoding="utf-8")
    cache = _load_cache(cache_dir)
    results: list[ClassifiedSurvivor] = []
    dirty = False

    for m in survivors:
        if not m.diff:
            results.append(ClassifiedSurvivor(m, "real_gap", "no diff available"))
            continue
        h = _diff_hash(m.diff)
        if h in cache:
            entry = cache[h]
            results.append(
                ClassifiedSurvivor(m, entry.get("verdict", "real_gap"), entry.get("reason", ""))
            )
            continue

        try:
            resp = llm.complete(
                "planner",
                system=SYSTEM,
                user=USER_TEMPLATE.format(source=source, diff=m.diff),
            )
        except Exception as e:
            # Rate limit, network blip, auth error, whatever. Default to
            # `real_gap` (safe: at worst we waste generator tokens on an
            # unkillable survivor) and DON'T cache the failure so a later
            # retry can still get a real verdict.
            reason = f"planner call failed: {type(e).__name__}; defaulted to real_gap"
            results.append(ClassifiedSurvivor(m, "real_gap", reason[:200]))
            continue
        verdict, reason = _parse_verdict(resp.text)
        cache[h] = {"verdict": verdict, "reason": reason}
        dirty = True
        results.append(ClassifiedSurvivor(m, verdict, reason))

    if dirty:
        _save_cache(cache_dir, cache)
    return results
