# mutagen-qa — living project memory

Kept short. Update whenever a decision, scope, or phase changes. New agents
should be able to pick up work by reading only this file.

---

## 1. Project goal & the core loop

Build an agentic QA engineer for Python. Given a target module, produce a
pytest suite optimized for **bug-catching power** (mutation kill rate), not
number of tests. "Comprehensive" must be *bounded and proven*, so we let a
mutation engine (mutmut) score the suite and use surviving mutants as the
signal for what to test next.

Closed loop:

    1. Generate an initial Tier-1 test batch for the target.
    2. Run pytest + coverage.
    3. Run mutmut: inject bugs, see which the suite catches.
    4. Surviving mutants = untested, bug-shaped gaps.
    5. Planner picks a technique per gap (tier 1 -> 2 -> 3) and asks the
       generator for targeted tests to kill those specific survivors.
    6. Repeat until kill rate plateaus (or budget hit).

Headline metric: high kill rate with a **lean** test count, e.g.
"94% kill rate / 40 tests" — engineering judgment, not test spam.

Tiers (agent escalates only when driven by survivors):
  - T1: happy-path, example-based, parameterized.
  - T2: boundary, error-path, negative, regression, characterization.
  - T3: property-based (Hypothesis), metamorphic, fuzz, concurrency, security.

---

## 2. Architecture overview

    src/mutagen/
      agent/       loop driver + planner + provider-agnostic LLM client
      mutation/    mutmut runner + typed MutationReport
      testgen/     tier1 / tier2 / tier3 test generators
      sandbox/     isolated pytest execution (subprocess or docker)
      mcp/         MCP server exposing qa.* tools
      eval/        benchmark harness + metrics
      cli.py       typer entrypoint
      config.py    central pydantic config
    benchmarks/    target repos with seeded bugs (empty for now)
    tests/         tests for THIS tool itself

Data flow per round:

    planner --spec--> testgen -> emit pytest source
        -> sandbox runs pytest + coverage
        -> mutation.runner runs mutmut, returns MutationReport
        -> planner reads survivors + coverage, picks next spec/tier
        -> loop stops on plateau / budget.

Boundaries are intentional so components stay swappable:
  - LLM provider (Ollama vs API) is one config knob.
  - Sandbox backend (subprocess vs docker) is one config knob.
  - MCP layer is an optional adapter, not a dependency of the core loop.

---

## 3. Key decisions log

- **Optimize kill rate, not test count.** Test count is a proxy that
  incentivizes spam. Mutation score directly measures the property we care
  about ("would this suite catch a real bug?").
- **mutmut over cosmic-ray.** Mature, Windows-friendly, active. Cosmic-ray
  gives finer control but adds infra we don't need for Phase 1.
- **litellm as the LLM abstraction.** One config for local Ollama, Ollama
  Cloud, and hosted APIs — provider swaps are a config edit.
- **Two LLM roles, not one model.**
  - `codegen` (writes pytest source) → **Ollama Cloud
    `qwen3-coder:480b-cloud`** by default. Frontier open-source coder,
    256K context; the specialist for the bandwidth-heavy job.
  - `planner` (decides which mutant to attack + which tier/technique) →
    **Gemini 2.5 Pro** (`gemini/gemini-2.5-pro`) by default. Smaller,
    higher-stakes calls where reasoning quality matters more than throughput.
  - Zero-cost fallback: point `codegen` at local `ollama/qwen3-coder:30b`
    without changing any code.
- **Subprocess sandbox first, Docker later.** Windows-friendly, no daemon
  dependency, good enough for Phase 1. `sandbox` is pluggable so Docker
  slots in without touching the loop.
- **MCP is an adapter, not the core.** The agent loop must be usable
  standalone; MCP is the last layer, added once the loop is proven.
- **Tests-for-this-tool live in `tests/`.** The `benchmarks/` tree is for
  *targets under test*, not our own unit tests.

---

## 4. Current phase & status

Phase 3 — **exit criterion met on 2/3 seeded targets. Seeded-bug corpus + scoring live; harness end-to-end verified against real Ollama Cloud calls.**

Live verification (2026-07-10):
  - `mutagen run benchmarks/phase1/target.py --max-rounds 1`: **96.6%** on
    `parse_range` -- matches recorded baseline exactly.
  - `mutagen run benchmarks/phase2/target.py --max-rounds 3`: **90.3% (T1)
    -> 93.5% (T2) -> 93.5% (T2)**. Same two true-equivalent survivors as
    before (`< 0` vs `<= 0` on a zero-baseline path). Repair fired in
    rounds 2+3 and rescued the pytest failures.
  - `mutagen bench benchmarks/seeded --max-rounds 3`:
      * `date_validate`: 100% kill rate, 3/3 seeded bugs caught.
      * `pagination`: 88.2% kill rate, 3/3 seeded bugs caught.
      * `csv_row`: pytest failed in round 1 -- codegen model
        (qwen3-coder) generated test cases whose expected outputs
        disagree with the parser's documented spec on ambiguous edge
        inputs like `'"""'`. The one-shot repair pass tried but couldn't
        reconcile the LLM's CSV mental model with the spec. Loop
        correctly bailed with `stopped_reason="pytest failed in round 1"`.
        This is a *codegen-model quality issue on an ambiguous spec*,
        not a mutagen bug -- a stronger codegen model (Gemini for
        codegen; GPT-4-class) would likely handle it.
      * Mean kill rate (on the 2 that ran): **94.1%**.
      * Mean seeded-bug catch rate (on the 2 that ran): **100%**.
  - Planner (Gemini) still rate-limited on free tier; classifier's error
    catch (per-survivor try/except) let the loop keep running with
    `real_gap` defaults. All prior classifier-crash / coverage-schema
    bugs remain fixed.
  - 69 unit tests passing (was 65; added 4 for seeded-bug scoring +
    discover_targets ignoring `bugs/` folders).

Done in Phase 3 THIS session:
  - `benchmarks/seeded/{csv_row,pagination,date_validate}/` — 3 targets,
    each with `target.py`, `bugs/bug_1..3.py`, and `bugs.json`. Nine
    seeded bugs total, hand-verified to diverge behaviorally from clean.
    Targets chosen for distinct shapes: string state machine, numeric
    edge cases, branchy validation.
  - `eval/benchmark.py::score_seeded_bugs` — swaps each `bug_N.py` into
    `workdir/target.py`, reruns the generated pytest suite, records
    caught/missed. Restores clean target in a `try/finally` so workdirs
    stay reusable. Only runs when the loop's own pytest is green.
  - `eval/benchmark.py::discover_targets` — now skips `target.py` files
    that live under a `bugs/` folder, so buggy variants aren't picked up
    as independent benchmark targets. Regression test added.
  - `BenchmarkEntry.seeded_bugs`, `.seeded_bug_catch_rate`;
    `BenchmarkReport.mean_seeded_bug_catch_rate`; `benchmark.json` gains
    both fields.
  - CLI `mutagen bench` gains a `seeded_bugs` column + mean seeded-bug
    catch rate line.
  - Tests: `test_score_seeded_bugs_measures_catch_rate` (direct scorer,
    no LLM), `test_score_seeded_bugs_returns_empty_when_no_corpus`,
    `test_run_benchmark_populates_seeded_bugs` (end-to-end via FakeLLM),
    `test_discover_targets_ignores_bugs_dir`. All hermetic.

Observed but not fixed:
  - qwen3-coder is fine on numeric/branchy targets but stumbles on CSV
    quote/escape edge cases where the spec is subtle. Route codegen to
    a stronger model when the target is a parser with ambiguous escapes.
  - Rich console renders raw pytest output through markup; `[i+1]`-style
    subscripts get eaten as unknown markup tags. Cosmetic only -- the
    actual target files on disk are untouched. Fix: wrap pytest panels
    in `Text.from_ansi(..., end="")` or `Console(markup=False)`.


Prior live verification (2026-07-08):
  - `mutagen run benchmarks/phase1/target.py --max-rounds 1`: **96.6%** on
    `parse_range` -- matches recorded baseline exactly.
  - `mutagen run benchmarks/phase2/target.py --max-rounds 3` (planner routed
    to Ollama via env override): **87.1% (T1) -> 93.5% (T2) -> 93.5% (T2)**.
    Repair path fired on all 3 rounds and successfully rescued each pytest
    failure. Both remaining survivors are true equivalents (a `<0` vs `<=0`
    check on an already-zero integer path), verified by hand. Total spend:
    codegen=8877 tok, planner=3090 tok.
  - `mutagen bench benchmarks/ --max-rounds 2`: mean **95.1%** across both
    targets, aggregate `benchmark.json` written.
  - `--json` output flag verified.
  - Two live bugs discovered and fixed:
    1. `mutation/coverage.py::load_coverage` was treating
       `summary.missing_lines` (an int COUNT in the real pytest-cov JSON) as
       a list -- crashed round 1 on every real run. Now reads only the
       per-file `missing_lines` list. Regression test added.
    2. `agent/classifier.py::classify_survivors` propagated litellm errors
       (rate-limit, network) and killed the whole loop. Now catches per
       survivor and defaults to `real_gap` (safest under uncertainty). Does
       NOT cache the failure so a later retry can still get a real verdict.
       Regression test added.
  - `config.py` gained env-var overrides
    (`MUTAGEN_{CODEGEN,PLANNER}_{MODEL,API_BASE,API_KEY_ENV}`) as the
    documented escape hatch for provider outages -- discovered when Gemini
    free-tier quota was at 0 and we needed to reroute the planner to Ollama.
  - Total tests: 65 passing.

Observed but not fixed:
  - Qwen3-Coder is a weak semantic-equivalence classifier. It flagged both
    of the phase-2 equivalent mutants as `real_gap`. When Gemini quota
    returns, the planner should route back there for classification. This
    is a *model-choice* issue, not a code bug.
  - T3 escalation is gated by `max_rounds`: if the plateau lands on the
    final round, we hit the cap before escalating. Fine for now, but users
    running with tight budgets should bump `max_rounds` by 1 to leave room
    for a T3 attempt.

Done in Phase 3 so far:
  - `testgen/tier3.py` — Hypothesis property-based generator. Same one-shot
    shape as tier1/tier2; reuses `_render_specs`.
  - `agent/loop.py` — plateau now triggers ONE T3 escalation instead of an
    immediate stop; a second plateau after T3 ends the run. Escalation is
    tracked via `t3_used` + `next_tier` locals so the state is dead-obvious
    when reading the loop.
  - `sandbox/executor.py::run_pytest` — takes `coverage_source=` and emits
    `coverage.json` via pytest-cov.
  - `mutation/coverage.py` — parses coverage.json, returns per-file executed /
    missing line lists. Malformed / missing report yields `{}`.
  - `agent/planner.py` — `plan_specs(missing_lines=...)` clips uncovered lines
    to each cluster's span and attaches them to the `TestSpec`. tier2 / tier3
    prompts include a "these lines aren't executed yet" hint per spec.
  - `agent/loop.py` — `_run_round` gets an optional `llm` for repair; a failed
    first pytest triggers a single `testgen/repair.py` shot, and the round
    proceeds if the repaired module runs. `RoundResult.repaired` records it.
  - `agent/testing.py::FakeLLM` — canned-response stub so tests exercise the
    full loop / benchmark harness with zero API keys.
  - `agent/llm.py::Usage` — per-role prompt / completion / call counters on
    every LLM instance. `RoundResult.usage` = delta per round; `LoopResult.
    total_usage` aggregates. FakeLLM tracks it too so tests can assert cost.
  - `MutationReport.to_dict`, `RoundResult.to_dict`, `LoopResult.to_dict`.
    `run_loop` writes `round_N_report.json` per round and `run.json` at exit
    (via try/finally, so early aborts still persist). CLI gains `--json PATH`.
  - `eval/benchmark.py` — walks a folder for `target.py`, runs the loop per
    target with isolated workdirs, tolerates per-target crashes (recorded as
    `error` on the entry), writes aggregate `benchmark.json`.
  - `cli.py` — new `mutagen bench <root>` command; per-round table now shows
    `repaired` + tokens; final `[LLM spend]` line.
  - Total tests: 60 passing (added: loop orchestration, usage, coverage,
    persistence, benchmark).

Web dashboard (2026-07-10):
  - `mutagen web` -- FastAPI + Jinja + HTMX + Chart.js. Read-only over
    the on-disk `runs/` folder; no build step; CDN Prism.js for
    syntax highlighting. Dark terminal-adjacent aesthetic.
  - Routes: `/` (runs list + benchmark aggregates), `/runs/{name}`
    (kill-rate curve, per-round table, target source, per-round test
    file viewer via HTMX, survivor diff list), `/bench/{name}` (mean
    kill rate + mean seeded-bug catch rate + per-target rows with
    expanded seeded-bug tables), `/api/runs/{name}` (raw run.json).
  - Path traversal guard: `_resolve_run` checks resolved path stays
    under `runs_root`. Unit-tested with URL-encoded `..`.
  - 10 route tests via FastAPI TestClient over a hand-built runs/ tree.
  - Live-verified against the actual runs on disk: all pages render,
    chart shows 90.3% -> 93.5% -> 93.5% on the phase2 run, bench view
    shows both good targets and the csv_row failure with correct
    stopped_reason.
  - Total tests: 79 passing (was 69; +10 for the web routes).

Phase 4 work landed (2026-07-11):
  - `agent/llm.py::LLM.complete` -- 3-attempt exponential backoff on
    transient errors (rate-limit / timeout / connection). `_is_retryable`
    matches by exception class name (litellm re-exports at unstable
    paths) with a message-substring fallback. Permanent errors (e.g.
    auth) do NOT retry. Test seam: `LLM._sleep = time.sleep` so tests
    capture backoff durations without real waits.
  - `testgen/repair.py` -- two-shot repair. Attempt 0 keeps codegen's
    temperature; attempt 1 bumps +0.3 and appends "your earlier fix
    still failed, revisit assumptions" hint. Motivated by qwen3-coder
    looping on the same wrong CSV expectation twice. Temperature is
    restored via try/finally so a shared LLM instance keeps its config.
    FakeLLM has no `_cfg`; the temp bump is a `getattr` chain that
    no-ops cleanly on stubs.
  - `cli.py` -- pytest stdout/stderr panels now wrap raw output in
    `rich.Text` so `[i+1]`-style code isn't eaten as unknown markup.
  - `sandbox/executor.py` + `sandbox/docker.py` -- Docker backend.
    `run(..., backend="subprocess"|"docker")`; docker path routes to
    a container with cwd mounted at /work, `--network=none`,
    unprivileged user. `Dockerfile` at `docker/sandbox/`; image
    (`mutagen-sandbox:latest`) is auto-built on first use.
    `DockerNotAvailable` raised if docker CLI missing -- no silent
    fallback (the point of picking docker is isolation, not
    convenience). `MUTAGEN_SANDBOX_BACKEND=docker` env var flips
    `AppConfig().sandbox.backend`.
  - `mutation/runner.py` -- takes `backend=` and threads it through
    all four internal shell calls; swaps `sys.executable` for
    container-side `python` when in docker.
  - `agent/loop.py::_run_round` -- reads `cfg.sandbox.backend` and
    forwards to both `run_pytest` and `run_mutmut`.
  - `mcp/server.py` + `mutagen mcp` CLI -- FastMCP server exposing
    `qa_generate_tests(target_path, tier)`, `qa_mutation_score(
    target_path, tests_source)`, `qa_run_loop(target_path, max_rounds,
    workdir_name)`. Stdio transport by default (Claude Desktop /
    Cursor compatible), also `sse` / `streamable-http`. `create_server(
    runs_root, llm_factory=...)` is DI-friendly; tests drive it in-memory
    via `FastMCP.call_tool`.
  - `benchmarks/seeded/` grew from 3 to 5 targets: added
    `hex_color/hex_to_rgb` (parser with shorthand + '#' handling) and
    `interval/intervals_overlap` (closed-interval overlap with
    boundary semantics). 6 more seeded bugs (3 per target),
    hand-verified to diverge.
  - `eval/benchmark.py::run_ablation` + `mutagen bench --ablation` --
    (target x config) grid runner. Default configs are T1-only,
    T1+T2, full-tier. `ablation.json` + per-config aggregates
    (mean kill rate, mean seeded-bug catch rate, mean tokens,
    n_targets). Each cell is a fresh isolated workdir so tests
    from one config never leak into another's mutmut score.
  - `README.md` rewritten from "Phase 0 scaffolding" to a real
    getting-started: install, one-command demo, dashboard, corpus,
    ablation, MCP, docker, config env vars.

Live smoke (2026-07-11):
  - `mutagen run benchmarks/seeded/hex_color/target.py --max-rounds 3`:
    **96.8% (T1) -> 96.8% (T2) -> 96.8% (T3)**. Repair fired on T3 and
    rescued it. Single true-equivalent survivor at `s[4:6]` vs
    `s[4:7]` (extends past the guaranteed-6-char string). Total spend:
    codegen=6867 tok, planner=0 tok (planner still rate-limited;
    classifier's per-survivor try/except kept the loop running).
  - **101 unit tests passing** (was 65 at start of the session).

Deployable web frontend (2026-07-11):
  - `web/jobs.py::JobRegistry` -- thread-based job store, no external
    dependency. Job workdirs land under `runs/<slug>-<id[:8]>/` so the
    read-only dashboard picks them up immediately -- no separate
    storage layer. Per-job bounded `queue.Queue` for SSE events; drops
    oldest on backpressure so a stuck client can't wedge the runner.
    Deliberate limits documented: not restart-safe, not multi-worker
    (swap to Celery+Redis when this hurts). Cancel is
    between-rounds (loop isn't preemptible mid-round).
  - `web/auth.py` -- opt-in bearer auth via `MUTAGEN_WEB_AUTH_TOKEN`.
    Unset = everything open (local dev default). Set = POST/DELETE
    require `Authorization: Bearer <token>` with constant-time compare;
    GETs stay open. WWW-Authenticate header on 401 for browser/curl UX.
  - `web/app.py` -- new routes: `GET /new` (form), `POST /jobs`
    (auth-gated; content-negotiates JSON vs 303 redirect), `GET /jobs`
    (list, cheap auto-refresh), `GET /jobs/{id}` (detail with live
    event log), `GET /api/jobs` / `GET /api/jobs/{id}` (JSON),
    `DELETE /api/jobs/{id}` (auth-gated cancel),
    `GET /api/jobs/{id}/events` (SSE: snapshot + per-round + done/
    failed/cancelled + end), `GET /healthz`.
  - Static assets served with `Cache-Control: public, max-age=86400,
    immutable`; `?v=` query for cache busting. SVG favicon.
  - Topnav on every page (runs / jobs / + new run). Same dark
    terminal aesthetic.
  - `create_app(runs_root=None)` -- reads `MUTAGEN_RUNS_ROOT` when
    runs_root is None, so `uvicorn --factory mutagen.web:create_app`
    works with zero args (the deployment shape).
  - `docker/web/Dockerfile` -- python:3.12-slim base, non-root user,
    pip install of the package + uvicorn[standard], EXPOSE 8000,
    HEALTHCHECK hitting `/healthz`, mounts `/data/runs` for
    persistence. Documented volume + auth + LLM-key env in comments.
  - Tests: `test_web_jobs.py` (15) covers health, form render, submit
    (JSON + redirect), input validation, jobs list, job detail SSE
    URL wiring, list/detail JSON APIs, cancel-404, live SSE snapshot
    + events + end, auth gating (401 without + with wrong token,
    201 with correct), static cache headers, favicon, topnav.
  - Live smoke: submitted `is_positive` and `double` targets via
    HTTP POST against the live server; both completed with 100% kill
    rate; SSE stream delivered snapshot -> started -> round -> done
    -> end in real time; artifacts appeared in the runs list without
    manual intervention.
  - **Total tests: 116 passing** (was 101; +15 for the write frontend).

Landing swap + per-round debrief (2026-07-11):
  - Landing page swap: `GET /` now serves the new-run form (most users
    want to start something, not browse). `GET /runs` serves the runs
    list. `GET /new` -> 308 redirect to `/` for back-compat. Topnav
    rearranged: brand -> `/`, links: runs, jobs; `+ new run` CTA
    kept for consistency though the landing already is the form.
  - `agent/debrief.py::write_round_body` -- writes
    `<workdir>/round_N_debrief.md` after each round with a
    human-readable narrative: pytest pass/fail (with which tests
    failed if failed), repair status (pass / still failing), mutmut
    result with surviving mutant diffs, coverage snapshot.
    `_parse_failing_tests` extracts `FAILED test.py::name - reason`
    lines from pytest short-report output. Bounded (first 20
    failures, reasons truncated to 200 chars).
  - `agent/debrief.py::append_handoff` -- appended to round N's
    debrief at the START of round N+1, once classifier + planner
    have run. Records verdict counts (real_gap / equivalent /
    message_noise) and the technique each cluster will be attacked
    with in the next round.
  - `RoundResult.initial_pytest_result` -- pre-repair pytest result,
    populated iff repair fired. Lets the debrief show what the LLM's
    first output actually broke on, alongside the final passing
    state. Backward-compatible default (None).
  - `web/markdown.py` -- in-tree Markdown -> HTML renderer covering
    headings, lists (nested), bold, italics, inline code, fenced
    code blocks with language class, HR. HTML-escape by default.
    No new dependencies for one page.
  - `web/app.py` -- new routes:
      * `GET /runs/{name}/debrief/{i}` -- rendered HTML.
      * `GET /runs/{name}/debrief/{i}/raw` -- raw markdown for
        copy/paste into a PR body.
  - `run_detail.html` -- new "debrief" column per round row linking
    to the rendered debrief page.
  - Circular import fix: `debrief` module uses TYPE_CHECKING for
    the `RoundResult` type hint; runtime access is duck-typed.
  - `TestSpec.__test__ = False` -- pytest was warning about
    collecting the dataclass; explicit opt-out.

Live smoke: submitted a small `clamp` target via the frontend at
`GET /` (the new landing). Both rounds produced
`round_N_debrief.md` files with the full narrative including the
"2 surviving mutants of kind comparison at lines 7-8" + the
handoff section showing "next round will target 1 cluster with
boundary-tests technique". HTML rendering, raw markdown fetch, and
the `/new` redirect all verified over HTTP.
**Total tests: 136 passing** (was 116; +20 for markdown renderer,
debrief writer, landing swap, debrief routes).

GitHub repo ingestion + multi-language plumbing (2026-07-11):
  - `src/mutagen/repo/clone.py::clone_repo` -- shallow git clone helper.
    Validates URL (https:// or file:// only; SSH / SCP-style rejected --
    server deployments would need agent forwarding). Rejects non-empty
    destinations. Enforces a timeout so a hanging remote can't wedge a
    job. `CloneError` surfaces to callers.
  - `src/mutagen/repo/detect.py::detect_languages` -- filesystem-walk
    census keyed by file extension. Covers all six roadmap languages
    (Python, JS, TS, Java, C#, C++). Skips `.git`, `node_modules`,
    `__pycache__`, `venv`, `build`, `target`, `bin`, `obj`, and other
    package-manager / VCS / build directories. Returns
    `{language: count}` sorted by descending count.
  - `web/jobs.py` -- `JobRegistry.submit` now takes either
    `target_source` (paste mode) OR `(repo_url, repo_target_path)`
    (repo mode), exactly one. The runner clones into `workdir/_repo/`,
    runs `detect_languages`, emits `cloning` / `languages` /
    `target_selected` events on the SSE stream, resolves the target
    path safely (rejects traversal, rejects non-.py in the current
    Python-only pipeline), copies it into the `_input.py` sidecar,
    and hands off to the existing loop unchanged.
  - `web/jobs.py` also drops a scoped `pytest.ini` into the workdir
    (`testpaths = .`, `norecursedirs = _repo`) so pytest+mutmut don't
    walk the cloned repo's other .py files during test collection.
    This turned a 5+ minute hang into a 20-second successful run on a
    75-file repo.
  - `web/app.py::POST /jobs` accepts the new form fields; empty strings
    from HTML forms are normalized to None so submit()'s exactly-one-of
    check reads cleanly.
  - `templates/new_run.html` -- two-mode form with a tab toggle: "paste
    source" (existing) and "github repo" (new). Inputs in the hidden
    panel are `disabled` so the browser never submits both sets. The
    repo panel notes the Python-only-today constraint inline.
  - `templates/job_detail.html` -- when `job.repo_url` is set, shows
    a "repo / target / languages" meta panel; live SSE events populate
    a language census as soon as `detect_languages` returns.
  - **Live smokes (real GitHub):**
      * `hukkin/tomli` -> `src/tomli/_re.py` -- clone succeeded, 14
        Python files detected, target found. pytest failed cleanly
        because the module imports from sibling package (which the
        current single-file target model can't provide). The debrief
        captured this honestly. **Pipeline verified.**
      * `tanmay-devhub/AgenticQA` -> `benchmarks/phase1/target.py` --
        clone succeeded, 75 Python files detected, target found,
        loop ran to **96.6%** kill rate in ~20s, matches the recorded
        baseline exactly. **End-to-end success.**
  - Tests: `test_repo.py` (URL validation + real git roundtrip via
    `file://` + language-detection edge cases), `test_web_repo_jobs.py`
    (exactly-one-of enforcement, repo-mode end-to-end with a local
    git origin, traversal rejection, non-Python rejection with
    language census still populated). **Total tests: 150 passing**
    (was 136; +14 for repo + repo-jobs).

Not yet done:
  - Multi-file / package-context targets: real-world modules often
    import from siblings; the single-file target model breaks on
    those. Path forward: detect the target's package root inside the
    repo, mount that as the workdir instead of just the one file,
    have mutmut mutate only the requested file within the package
    context.
  - JS/TS/Java/C#/C++ pipelines. All plumbing (clone, detect, target
    selection, workdir isolation, SSE progress, debrief) is now
    language-agnostic -- next language is a `sandbox` image + a
    minimal `LanguagePlugin` implementation (generator prompt +
    test-runner argv + mutation-tool argv).
  - Live re-verify with Gemini as planner once free-tier quota resets
    (equivalent-mutant classification quality check).
  - Cost guardrails (hard token / USD budget with graceful stop).
  - Diff-driven / CI mode (regenerate only tests affected by a git diff).
  - Multi-function targets (target selection within a module).
  - Package/repo mode.
  - Persistent job DB + restart-safety (Celery / Redis swap).
  - Multi-worker uvicorn (would require the above).

### Prior: Phase 2 — complete. Multi-round loop wired end-to-end.

Done in Phase 2:
  - `Mutant` gained a coarse `kind` field
    (`comparison`/`arithmetic`/`constant`/`boolean`/`return`/`keyword`/`call`/`other`)
    plus real `file` / `line` values, parsed from the `mutmut show` diff by
    `mutation/runner.py::_classify_diff` and `_parse_file_and_line`. Numeric
    / bool / None literals are stripped BEFORE the operator regexes run, so
    `-1 -> 2` reads as `constant`, not `arithmetic`.
  - `agent/classifier.py` — LLM-backed survivor classifier tags each unique
    survivor as `real_gap | equivalent | message_noise`. Results cached by
    SHA-1 of the diff in `<workdir>/.mutagen/classifier_cache.json` so
    multi-round loops pay Gemini once per unique survivor. Malformed LLM
    output falls back to `real_gap` (never silently drops a real survivor).
  - `agent/planner.py` — rule-based planner v0. Drops non-`real_gap`
    survivors, clusters remainders by `(file, function)`, maps each cluster's
    dominant kind to a technique hint for T2.
  - `testgen/tier2.py` — targeted generator: takes planner specs and asks the
    codegen role to kill the specific listed survivors.
  - `agent/loop.py` — refactored to `LoopResult` with a list of `RoundResult`.
    Round 1 runs T1; rounds 2..N run classify -> plan -> T2 -> pytest -> mutmut.
    Stops on: pytest fail, no survivors, no `real_gap` survivors, plateau
    (`kill_rate` delta < `loop.plateau_delta`), `max_rounds`, or wall-clock.
  - `config.LoopBudget.max_rounds` default bumped 1 -> 3.
  - CLI prints a per-round table plus the final report; `--max-rounds` can
    force one-shot behavior for debugging.
  - Second phase-2 target: `benchmarks/phase2/target.py::discounted_price`
    (multi-arm tier arithmetic + numeric returns, no strings-as-behavior).
  - 39 unit tests: report math, diff classifier, `_strip_fences`, planner
    clustering, JSON verdict parser (with safe fallbacks).

Not yet verified: live end-to-end multi-round run against Ollama Cloud +
Gemini. Needs `OLLAMA_API_KEY` + `GEMINI_API_KEY` in `.env`.

### Prior: Phase 1 — complete. Loop proven end-to-end.

Done in Phase 1:
  - Target: `benchmarks/phase1/target.py::parse_range` (spec parser with
    branching + exception paths + negative-number edge case).
  - `sandbox/executor.py`: subprocess backend with UTF-8 forced env
    (fixes mutmut cp1252 emoji crash on Windows).
  - `mutation/runner.py`: invokes mutmut with `--runner` pointing at
    `sys.executable` (fixes Windows `python`-shim ambush), pulls counts
    via `mutmut result-ids <status>` (more reliable than scraping the
    progress bar), fetches survivor diffs via `mutmut show`.
  - `mutation/report.py`: typed `MutationReport` + `format_summary`.
  - `testgen/tier1.py`: prompts codegen role, strips code fences.
  - `agent/llm.py`: litellm-backed two-role client.
  - `agent/loop.py`: one-shot orchestration.
  - `cli.py`: `mutagen run <target>` with rich output.

Phase 1 exit criteria met. Sample run against `parse_range`:

    killed=28/29  survived=1  timeout=0  suspicious=0  kill_rate=96.6%
    filtered mutation types: string,fstring

Generated 4 test functions (26 parametrized cases) in one LLM shot; pytest
green; mutmut ran 29 behavioral mutants.

String-mutation handling (decision: Option A -- disable at source):
  - `MutationConfig.disabled_types` defaults to `["string", "fstring"]`.
    Pure string-literal mutations only change error-message wording, which
    our generator (correctly) refuses to assert on.
  - CLI escape hatch `--strings-are-behavior` for template renderers /
    URL builders / format-string code where strings ARE semantics.
  - Filtered types are surfaced in the report line so filtering is never
    silent.
  - Kill rate jumped from 78.9% (raw) -> 96.6% (behavioral).

The one remaining survivor (`right[1:]` -> `right[2:]` in the double-negative
branch) is a near-equivalent mutant: it reaches a different code path but
ends at the same `ValueError`. Killable only by asserting message text,
which we forbid.

Verified end-to-end on a second target, `benchmarks/examples/clamp.py`:

    killed=1/3  survived=2  kill_rate=33.3%
    survivors: `v < lo` -> `v <= lo`  and  `v > hi` -> `v >= hi`

INITIALLY MISREAD as "LLM missed the v==lo / v==hi boundary." Reading the
generated test file showed the LLM DID cover both boundaries -- so why did
the mutations survive? Because at `v == lo`, `clamp` returns `v` (original)
or `lo` (mutated), and `v == lo`, so both return the same value. These are
**fully equivalent mutants** -- unkillable by any `==`-based test.

Consequence: there are TWO classes of raw-mutation-score noise:
    1. String-literal mutations (filtered at source via disabled_types).
    2. Semantically equivalent mutants (harder -- can't filter at source).

Class 2 is a Phase 2 planner problem: before feeding a survivor back to the
test generator as a "gap," the planner must classify it as `real gap` /
`equivalent` / `message noise`. Otherwise Phase 2 will chase unkillable
ghosts forever, inflating LLM cost with no kill-rate gain. Candidate
approach: LLM-based classifier fed the diff + surrounding code; treat as
per-round overhead paid once per unique survivor.

---

## 5. Phased build plan

### Phase 1 — prove the loop (smallest vertical slice)

Goal: on ONE hand-picked target file, run generate -> pytest -> mutmut ->
kill-rate report, end to end. No planner intelligence, no tier escalation,
no MCP.

Scope:
  - Ship `mutation.runner` + `mutation.report` for real: run mutmut on a
    given (source, tests) pair and parse survivors.
  - Ship `sandbox.executor` subprocess backend with timeout.
  - Ship `testgen.tier1` calling an LLM (litellm) once to produce a
    parameterized pytest file from source + docstring.
  - Ship `agent.loop` as a **one-shot**: generate T1 tests, run them,
    mutate, print kill rate. Loop count = 1.
  - Pick ONE target: put a tiny hand-written module in
    `benchmarks/phase1/target.py` (e.g. a bounded integer parser or a
    line-splitter — small, has real edge cases).
  - Wire `mutagen run <target>` in `cli.py`.

Exit criteria: `mutagen run benchmarks/phase1/target.py` prints
  `killed=N/M  survived=K  kill_rate=X%` and dumps generated tests +
  survivor list. That's it.

Explicitly NOT in Phase 1: planner, T2/T3, MCP, benchmark suite, Docker
sandbox, multi-round loop, plateau detection.

### Phase 2 — close the loop

  - Structured survivor -> spec extraction (planner v0: rule-based map
    from mutmut mutation kinds to techniques).
  - Multi-round loop with plateau detection + budget.
  - T2 generator (boundary/error/negative).
  - Second target file to guard against overfitting the loop to Phase 1's
    target.

### Phase 3 — property tests + real benchmark

  - T3 generator (Hypothesis strategies from signatures/docstrings;
    metamorphic relations).
  - `benchmarks/` grows to 3-5 targets with **seeded bugs**; `eval/`
    reports kill rate, test count, LOC, wall-clock, seeded-bug detection.
  - Ablations: T1-only vs T1+T2 vs full-tier — does escalation actually
    pay?

### Phase 4 — MCP + polish

  - MCP server exposing `qa.generate_tests`, `qa.mutation_score`,
    `qa.run_loop` (streamed).
  - Docker sandbox backend.
  - Docs, example configs, one demo GIF.

---

## 6. Next steps / open questions

Immediate:
  - Get plan reviewed. **Do not start Phase 1 until approved.**

Open questions to resolve before / during Phase 1:
  - LLM defaults locked: codegen = Ollama Cloud `qwen3-coder:480b-cloud`,
    planner = `gemini/gemini-2.5-pro`. Env vars required at run time:
    `OLLAMA_API_KEY`, `GEMINI_API_KEY`. Confirm both are set before Phase 1.
  - mutmut config: mutate the whole target file, or narrow to functions
    the planner names? (Phase 1: whole file for simplicity.)
  - Sandbox: subprocess timeout default? Proposed 30s per pytest run,
    120s per mutmut run — revisit under real workloads.
  - Equivalent-mutant handling: ignore for Phase 1, address in Phase 2
    (surface as "suspected equivalent" and let user mark).
  - Target picking for Phase 1: propose a small pure-Python function
    with real branching (e.g. `parse_range("3-7")` returning `range(3,8)`
    with edge cases). Alternatives welcome.
