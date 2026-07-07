# mutagen-qa

Agentic QA engineer for Python codebases. Generates test suites optimized for
**bug-catching power** (mutation kill rate), not test count.

Core loop: generate tests → run pytest + coverage → run mutmut → surviving
mutants steer the next round of test generation, escalating from happy-path
(Tier 1) through edge/error paths (Tier 2) to property-based / metamorphic /
fuzz (Tier 3). Stops when kill rate plateaus.

See `../CONTEXT.md` for the living design doc, decisions, and current phase.

## Status
Phase 0: scaffolding only. No functionality yet.
