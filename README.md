# mutagen-qa

**An agentic QA engineer for real code.** Point it at a source file — soon,
any GitHub repo — and it generates a test suite optimized for **bug-catching
power** (mutation kill rate), not test count.

Ships end-to-end today for **Python**. Multi-language support
(JavaScript, TypeScript, Java, C#, C++) and GitHub-repo ingestion are the
next milestone.

---

## Why mutation-guided?

Traditional test-generation tools optimize for line coverage. But 100%
coverage says nothing about whether the tests would *notice* a real bug.
Mutation testing measures that directly: inject a small change into the code
(`>` → `>=`, `+` → `-`, delete a `raise`, …), rerun the suite, and see if
any test fails. A **survivor** — a mutation the tests missed — is a
bug-shaped gap in coverage. The higher the **kill rate**, the more real
defects your suite would catch.

`mutagen-qa` closes the loop:

    generate tests → run pytest + coverage → run mutmut →
      classify survivors (real gap | equivalent | message-noise) →
      plan next round (T1 → T2 → T3) → repeat until plateau

The output is a **lean** pytest suite (typically 20–50 tests) with a
defensible kill rate, plus a per-round debrief file you can paste into a PR
body.

---

## Quickstart

```bash
git clone <this-repo>
cd mutagen-qa
uv venv --python 3.12 .venv
uv pip install -e .
```

Put your keys in `.env`:

```
OLLAMA_API_KEY=...
GEMINI_API_KEY=...
```

Then, on Windows:

```powershell
.\mutagen.bat run benchmarks\phase1\target.py --max-rounds 3
.\mutagen.bat web            # dashboard at http://127.0.0.1:8765
```

(Or `python -m mutagen.cli …` on any platform.)

---

## Features

### Core loop
- **Three-tier test generation** — T1 (happy-path parameterized) → T2
  (boundary / error / negative, driven by classified survivors) → T3
  (Hypothesis property-based, escalation on plateau).
- **Survivor classifier** — separates *real gaps* from *equivalent
  mutants* and *message-noise*, cached by diff hash so multi-round loops
  never re-pay for the same survivor.
- **Two-shot repair** — if generated tests fail pytest, the LLM gets two
  attempts (temperature bumped on the second) to fix its own output
  before the round aborts.
- **Coverage-guided planning** — uncovered lines from `coverage.json` are
  fed into T2/T3 prompts as targeted hints.
- **LLM retry-with-backoff** — 3 attempts on rate-limit / timeout /
  network errors, fail-fast on permanent errors (auth, bad request).
- **Provider-agnostic** — swap `codegen` or `planner` provider with a
  single env var (`MUTAGEN_CODEGEN_MODEL`, `MUTAGEN_PLANNER_MODEL`).

### Interfaces
- **CLI** — `mutagen run`, `mutagen bench`, `mutagen web`, `mutagen mcp`,
  `mutagen version`.
- **Web dashboard** (`mutagen web`) — new-run form on `/`, live per-round
  progress via SSE, kill-rate chart, generated-test viewer, survivor
  diff browser, per-round debrief pages, benchmark aggregates with
  seeded-bug catch rate. Optional bearer-token auth for deployment
  (`MUTAGEN_WEB_AUTH_TOKEN`).
- **MCP server** (`mutagen mcp`) — exposes `qa_generate_tests`,
  `qa_mutation_score`, `qa_run_loop` for Claude Desktop / Cursor / any
  MCP client. stdio, SSE, and streamable-HTTP transports.
- **Docker sandbox** — set `MUTAGEN_SANDBOX_BACKEND=docker` to run
  untrusted target code inside `mutagen-sandbox:latest` (unprivileged
  user, `--network=none`, workdir mounted at `/work`).
- **Container deployment** — `docker/web/Dockerfile` builds the web app;
  mount `/data/runs` for persistence.

### Evaluation
- **Seeded-bug corpus** at `benchmarks/seeded/` — 5 targets across
  distinct shapes (parser, numeric edges, branchy validation, string
  state machine, comparison logic), each with hand-crafted bugs and a
  `bugs.json` manifest. The harness swaps each buggy variant into the
  workdir target and re-runs the generated suite so you can measure
  seeded-bug catch rate alongside kill rate.
- **Ablation harness** — `mutagen bench <root> --ablation` runs each
  target under T1-only / T1+T2 / full-tier and reports whether the
  extra tiers earn their tokens.

---

## Multi-language support (roadmap)

Today the pipeline is Python-only. Each language will ship end-to-end
before the next starts. All six candidates below have production-grade
mutation tools available — none get skipped:

| Language   | Mutation tool  | Test runner        | Status  |
|------------|----------------|--------------------|---------|
| Python     | mutmut         | pytest             | shipped |
| JavaScript | Stryker        | jest / vitest      | planned |
| TypeScript | Stryker        | jest / vitest      | planned |
| Java       | PIT            | JUnit              | planned |
| C#         | Stryker.NET    | dotnet test / xUnit| planned |
| C++        | mull           | gtest / catch2     | planned |

**GitHub-repo ingestion** is on the same track: paste a GitHub URL,
mutagen clones the repo locally, detects languages by file extension
(with a small hand-tuned override list for edge cases), runs each
language's loop in parallel where the sandbox permits, and streams
per-language progress to the dashboard.

---

## What input looks like today

Any Python file with a docstring on each public function. The docstring is
the **oracle** — it tells the codegen model what "correct" means.

```python
def slugify(text: str, max_len: int = 60) -> str:
    """Turn text into a URL-safe hyphen-separated slug.
    - Empty or all-punctuation input -> empty string.
    - Truncates to max_len, then re-trims trailing hyphens.
    - Non-str input -> TypeError.
    """
```

Run:

```powershell
.\mutagen.bat run slugify.py --max-rounds 3
```

## What lands on disk after a run

```
runs/slugify-20260710-153000/
    target.py                  # copy of the input
    test_round_1.py            # T1 generated tests
    test_round_2.py            # T2 (targeted at classified survivors)
    test_round_3.py            # T3 (Hypothesis, if plateau triggered)
    round_1_debrief.md         # human-readable round narrative
    round_2_debrief.md
    round_3_debrief.md
    round_1_report.json        # machine-readable per-round detail
    round_2_report.json
    round_3_report.json
    coverage.json              # pytest-cov output
    run.json                   # full LoopResult (kill rates, tokens, …)
```

Each `round_N_debrief.md` walks you through the round: which tests failed
initially (if any), what the repair did, which mutants survived (with
diffs), and how the *next* round classified them and which technique it
chose to attack them.

---

## Configuration

Every knob is an env var — swap providers, sandboxes, and auth without
editing code:

| Variable                                              | Purpose                                            |
|-------------------------------------------------------|----------------------------------------------------|
| `MUTAGEN_CODEGEN_MODEL` / `MUTAGEN_PLANNER_MODEL`     | swap the litellm model string per role             |
| `MUTAGEN_CODEGEN_API_BASE` / `MUTAGEN_PLANNER_API_BASE` | point at a different endpoint                    |
| `MUTAGEN_CODEGEN_API_KEY_ENV` / `MUTAGEN_PLANNER_API_KEY_ENV` | look up the key under a different env var |
| `MUTAGEN_SANDBOX_BACKEND`                             | `subprocess` (default) or `docker`                 |
| `MUTAGEN_WEB_AUTH_TOKEN`                              | when set, gates `POST /jobs` + `DELETE /api/jobs/*` |
| `MUTAGEN_RUNS_ROOT`                                   | override the `runs/` folder location               |

Provider SDKs use their standard key names (`OLLAMA_API_KEY`,
`GEMINI_API_KEY`, `OPENAI_API_KEY`, …).

---

## Architecture

```
src/mutagen/
    agent/       loop driver, LLM client, survivor classifier, planner, debrief
    mutation/    mutmut runner + typed MutationReport + coverage parsing
    testgen/     T1 / T2 / T3 generators + two-shot repair
    sandbox/     subprocess + Docker backends behind one interface
    mcp/         FastMCP server exposing qa_* tools
    eval/        benchmark harness + ablation grid + seeded-bug scorer
    web/         FastAPI dashboard + jobs API + SSE + opt-in auth
    cli.py       typer entrypoint
    config.py    central pydantic config (env-var overrides everywhere)
benchmarks/      target modules + seeded-bug corpus
docker/          sandbox + web deployment images
tests/           136 tests for this tool itself
```

Living design doc, decision log, phase-by-phase status, and known caveats:
[`../CONTEXT.md`](../CONTEXT.md).

---

## Development

```bash
uv pip install -e ".[dev]"
pytest -q                          # 136 tests, ~10s on a warm cache
ruff check .
mypy src
```

Contributions welcome once multi-language support lands.

---

## Status

Phase 3 complete: web frontend + MCP + Docker sandbox + ablation harness
shipped end-to-end. Live-verified against real LLM APIs on the seeded
corpus and on ad-hoc user-submitted targets. Next: GitHub-repo ingestion
and JavaScript / TypeScript / Java / C# / C++ pipelines.
