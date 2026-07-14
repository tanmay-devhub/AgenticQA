# mutagen-qa-js

JavaScript sibling of the Python `mutagen` package (both live in this repo).
Point it at a JS source file and it generates a `node --test` suite optimized
for **bug-catching power** (mutation kill rate), not test count.

Runs Stryker JS as the mutation engine and Node's built-in `node --test`
runner. Same three-role LLM setup (codegen / planner / analysis), same
`.env`, same `runs/` output layout as the Python pipeline, so the existing
FastAPI web dashboard reads JS runs unchanged.

## Quickstart

```bash
cd mutagen-qa-js
npm install
# .env lives at the repo root (../.env); the CLI walks upward to find it.
# Copy one in only if you want a JS-specific override.

# local file
node ./bin/mutagen-js.js run benchmarks/slugify/target.js --max-rounds 3

# clone a git URL, target a file inside
node ./bin/mutagen-js.js repo https://github.com/user/repo \
  --target-path src/util.js --max-rounds 3
```

Runs land at `<repo>/mutagen-qa-js/runs/<stem>-<ts>/` in the same JSON layout
the Python dashboard already understands: `target.js`, `test_round_N.js`,
`round_N_report.json`, `round_N_debrief.md`, `run.json`.

## Configuration

Same env-var scheme as the Python pipeline:

| Variable                    | Purpose                              |
|-----------------------------|--------------------------------------|
| `MUTAGEN_CODEGEN_MODEL`     | swap the codegen model               |
| `MUTAGEN_PLANNER_MODEL`     | swap the planner model               |
| `MUTAGEN_ANALYSIS_MODEL`    | swap the analysis model              |
| `MUTAGEN_CODEGEN_API_BASE`  | override the codegen endpoint        |
| `MUTAGEN_RUNS_ROOT`         | override the `runs/` output location |

Provider SDKs use their standard key names (`OLLAMA_API_KEY`,
`GEMINI_API_KEY`).

Optional flags:

- `--focus <text>` / `--focus-file <path>` — plain-English priority passed to
  every codegen call (written to `<workdir>/focus.txt`).
- `--max-rounds <N>` — integer in `[1, 10]`, default `3`.
- `--workdir <path>` — override the auto-generated `runs/<stem>-<ts>/` dir.

Supported inputs: `.js`, `.mjs`, `.jsx` (ESM only — the generated tests
`import` from `./target.js`). CommonJS (`.cjs`) and TypeScript are on the
roadmap.

## What's in

- **Tier 1** (happy-path + boundary) via `node:test`.
- **Tier 2** targeted at classified real_gap survivors, with per-mutant
  diff snippets fed back to the codegen prompt.
- **Tier 3** property-based tests via `fast-check`, escalated once on plateau.
- **Classifier** (real_gap / equivalent / message_noise) with parallel LLM
  calls (concurrency 4) and SHA-1-keyed cache written atomically to
  `<workdir>/.mutagen/classifier_cache.json`.
- **Rule-based planner** clustering survivors by file + line window into
  `TestSpec[]` with dominant-kind technique hints loaded from
  `../shared/schema/technique_by_kind.json` (shared with the Python planner).
- **Two-shot repair** on broken tests (temperature bumped on attempt 1).
- **Per-round debrief** (`round_N_debrief.md`) with test run / repair /
  Stryker / handoff sections.
- **Truncation detection** — if the codegen provider reports
  `finish_reason=MAX_TOKENS` (Gemini) or `length` (OpenAI-compat) the loop
  stops with a specific reason instead of silently continuing on a partial
  file.
- **Crash-safe `run.json`** — a `try/finally` sets `stopped_reason=crashed: …`
  and persists before re-raising, so the dashboard never sees a stale run.
- **Stryker mutation report** parsed into the same JSON shape as Python's
  `MutationReport` so the FastAPI dashboard renders JS runs unchanged.
  Cross-language kind taxonomy is enforced at import time against
  `../shared/schema/mutation_kinds.json`.

## Repo-mode safety

`repo` mode clones the URL into `<workdir>/_repo/`, then resolves
`--target-path` with `realpathSync` and asserts the resolved path is still
inside the clone directory. Symlinks that escape the clone (e.g. via a
crafted `.gitattributes`) are rejected before any test generation runs.

## Testing

```bash
npm test                # 65 pass, 1 skip (symlink test on non-admin Windows)
```

The `test/` suite covers CLI arg validation, `.env` upward search, repo
clone + symlink handling, classifier caching + parallelism, planner
clustering, repair fence-stripping, Stryker report parsing, debrief YAML
parsing, and the `LoopResult` aggregation shape.

## Status

Beta. Multi-round loop is fully wired and end-to-end verified against both
Ollama Cloud and Gemini. Items intentionally not implemented (Windows
long-path hardening, Stryker schema v3, jest-incremental runner) are
documented with revisit criteria in `../shared/DEFERRED.md`.
