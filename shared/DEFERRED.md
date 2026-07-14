# Deferred hardening

Items intentionally not implemented in the current batch; each is a real
concern but with low realized user impact today. Revisit when the underlying
condition shows up in a real run.

## 8. Windows path-length hardening (>260 chars)

**Symptom**: `ENAMETOOLONG` when workdirs land under deep paths and Stryker
writes to `reports/mutation/mutation.json` (~40 extra chars).

**Not implemented because**: current runs sit at ~150 chars. Windows 10+ with
`LongPathsEnabled` in the registry lifts the cap without code changes.

**When to implement**: first user report of ENAMETOOLONG. Fix would be to
adopt the `\\?\` prefix for absolute paths on Windows in `runSubprocess` and
`writeFileSync` targets under the workdir.

## 10. Stryker report v3 schema fallback

**Symptom**: silent zero counts if Stryker publishes a breaking schema
change.

**Status**: partial — we now emit a `console.warn` when `schemaVersion` isn't
"1" or "2" (`src/mutation/runner.js`). Full v3 support requires understanding
the shape once Stryker announces it.

**When to fully implement**: when Stryker ships v3.

## 23. Jest incremental (perTest coverage)

**Symptom**: Stryker's `command` runner reinvokes the whole test suite per
mutant. For 60 mutants with a ~1s suite that's 60s of wall clock.

**Not implemented because**: switching runners means adding jest as a peer
dep, teaching the codegen prompts to emit jest-compatible tests (not
`node:test`), and generating a jest config per workdir. Substantial rework
for a ~2x speedup on the mutation phase.

**When to implement**: when a real target's mutation phase exceeds 5 minutes
of wall clock, or when a user needs per-test coverage attribution for
debugging.
