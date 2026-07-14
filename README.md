# mutagen-qa

**An agentic QA engineer for real code.** Point it at a source file (or a
GitHub repo URL) and it generates a test suite optimized for **bug-catching
power** (mutation kill rate), not test count.

Two self-contained pipelines live in this repo, sharing only the
cross-language schemas and the design/decision log:

| Package                                   | Language   | Mutation tool | Test runner |
|-------------------------------------------|------------|---------------|-------------|
| [`mutagen-qa-py/`](mutagen-qa-py/)        | Python     | mutmut        | pytest      |
| [`mutagen-qa-js/`](mutagen-qa-js/)        | JavaScript | Stryker JS    | node --test |

Each has its own README, its own dependency manifest, its own tests, and
its own runs directory. Neither imports code from the other. The only
crossings are:

- **`shared/`** — cross-language JSON schemas (mutation-kind taxonomy +
  planner technique hints). Both languages import-check their internal
  constants against these files at startup, so a rename on one side fails
  loudly instead of silently drifting.
- **`.env`** — one file at repo root. Both CLIs walk upward from the cwd
  looking for it, so the same `OLLAMA_API_KEY` / `GEMINI_API_KEY` serve
  both pipelines.
- **The FastAPI dashboard** (in the Python package) spawns the JS CLI as
  a subprocess for JS jobs. Both languages emit the same `runs/<stem>/…`
  JSON shape, so the dashboard renders them from the same code path.

## Why mutation-guided?

Traditional test-generation tools optimize for line coverage. But 100 %
coverage says nothing about whether the tests would *notice* a real bug.
Mutation testing measures that directly: inject a small change into the
code (`>` → `>=`, `+` → `-`, delete a `raise`, …), rerun the suite, and
see if any test fails. A **survivor** (a mutation the tests missed) is a
bug-shaped gap in coverage. The higher the **kill rate**, the more real
defects your suite would catch.

The loop closes it end-to-end:

    generate tests → run tests + coverage → run mutation engine →
      classify survivors (real gap | equivalent | message-noise) →
      plan next round (T1 → T2 → T3) → repeat until plateau

## Repo layout

```
mutagen-qa/                (git root)
├── README.md              (you are here)
├── CONTEXT.md             (design doc, decision log, phase-by-phase history)
├── .env / .env.example    (shared API keys)
├── .gitignore
├── shared/
│   ├── schema/
│   │   ├── mutation_kinds.json       (cross-language kind taxonomy)
│   │   └── technique_by_kind.json    (cross-language planner hints)
│   └── DEFERRED.md                   (items intentionally not implemented)
├── mutagen-qa-py/         (Python pipeline — see its README)
└── mutagen-qa-js/         (JavaScript pipeline — see its README)
```

## Multi-language roadmap

Each language ships end-to-end before the next starts:

| Language   | Mutation tool  | Test runner        | Status  |
|------------|----------------|--------------------|---------|
| Python     | mutmut         | pytest             | shipped |
| JavaScript | Stryker        | node --test        | shipped |
| TypeScript | Stryker        | jest / vitest      | planned |
| Java       | PIT            | JUnit              | planned |
| C#         | Stryker.NET    | dotnet test / xUnit| planned |
| C++        | mull           | gtest / catch2     | planned |

## Status

Python pipeline: complete, live-verified against real LLMs, 195 tests.
JavaScript pipeline: complete, live-verified against Ollama Cloud and
Gemini, 65 tests + 1 admin-only skip. Detailed run data, features, and
CLI usage live in each package's README.
