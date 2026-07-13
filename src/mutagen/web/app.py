"""FastAPI dashboard: browse runs/, benchmark artifacts, and (optionally)
submit new mutagen runs from the browser.

Design principles:
    - Read paths are safe against partial state (a workdir with only
      ``round_1_report.json``, no run.json, must render something useful).
    - Write paths (job submission, cancellation) are gated by
      ``require_write_auth``, which no-ops in local dev but requires a
      bearer token when ``MUTAGEN_WEB_AUTH_TOKEN`` is set.
    - Zero build step: Jinja + HTMX + Chart.js + Prism.js from CDN. If
      you want an SPA later, this app is the JSON+HTML fallback view.
    - ``runs_root`` and ``llm_factory`` are injectable so tests can
      point at tmp_path with a FakeLLM.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mutagen.agent.llm import LLM
from mutagen.config import AppConfig
from mutagen.mutation.report import Mutant, MutationReport
from mutagen.report import AnalysisReport, analyze_run
from mutagen.report.pdf import html_to_pdf
from mutagen.web.auth import is_auth_configured, require_write_auth
from mutagen.web.jobs import JobRegistry
from mutagen.web.markdown import render as render_markdown

_WEB_DIR = Path(__file__).parent
_TEMPLATE_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"


@dataclass
class RunSummary:
    """One row on the runs-list page. Everything is best-effort:
    a run with a truncated ``run.json`` still renders, just with blanks."""
    name: str
    path: Path
    target: str | None
    stopped_reason: str | None
    kill_rate: float | None
    rounds: int
    wall_clock_s: float | None
    total_tokens: int | None
    mtime: float

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "target": self.target,
            "stopped_reason": self.stopped_reason,
            "kill_rate": self.kill_rate,
            "rounds": self.rounds,
            "wall_clock_s": self.wall_clock_s,
            "total_tokens": self.total_tokens,
            "mtime": self.mtime,
        }


def _load_json(p: Path) -> dict | None:
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _scan_run(run_dir: Path) -> RunSummary | None:
    """Build a summary for one ``runs/<name>/`` folder. Returns None if the
    folder has no artifacts we can identify as a run."""
    run_json = _load_json(run_dir / "run.json")
    round_files = sorted(run_dir.glob("round_*_report.json"))
    if run_json is None and not round_files:
        return None

    target = None
    stopped = None
    kill_rate = None
    rounds = 0
    total_tokens = None
    wall_clock = None

    if run_json is not None:
        stopped = run_json.get("stopped_reason")
        kill_rate = run_json.get("final_kill_rate")
        rounds = len(run_json.get("rounds") or [])
        usage = run_json.get("total_usage") or {}
        cg = (usage.get("codegen") or {})
        pl = (usage.get("planner") or {})
        total_tokens = (
            (cg.get("prompt_tokens") or 0) + (cg.get("completion_tokens") or 0)
            + (pl.get("prompt_tokens") or 0) + (pl.get("completion_tokens") or 0)
        )
        wall_clock = sum((r.get("elapsed_s") or 0) for r in (run_json.get("rounds") or []))
    else:
        rounds = len(round_files)

    # Try to guess the target from the workdir's target.py header.
    target_py = run_dir / "target.py"
    if target_py.is_file():
        try:
            first_line = target_py.read_text(encoding="utf-8").splitlines()[0]
            target = first_line.lstrip('"# ').strip() or target_py.name
        except (OSError, IndexError):
            target = target_py.name

    return RunSummary(
        name=run_dir.name,
        path=run_dir,
        target=target,
        stopped_reason=stopped,
        kill_rate=kill_rate,
        rounds=rounds,
        wall_clock_s=wall_clock,
        total_tokens=total_tokens,
        mtime=run_dir.stat().st_mtime,
    )


def _scan_runs(runs_root: Path) -> list[RunSummary]:
    if not runs_root.is_dir():
        return []
    out: list[RunSummary] = []
    for child in runs_root.iterdir():
        if not child.is_dir():
            continue
        s = _scan_run(child)
        if s is not None:
            out.append(s)
    out.sort(key=lambda r: r.mtime, reverse=True)
    return out


def _scan_benchmarks(runs_root: Path) -> list[dict]:
    """A benchmark is any folder under runs/ containing a ``benchmark.json``."""
    if not runs_root.is_dir():
        return []
    out: list[dict] = []
    for child in runs_root.iterdir():
        if not child.is_dir():
            continue
        data = _load_json(child / "benchmark.json")
        if data is None:
            continue
        out.append({
            "name": child.name,
            "mtime": child.stat().st_mtime,
            "entries": data.get("entries", []),
            "mean_kill_rate": data.get("mean_kill_rate"),
            "mean_seeded_bug_catch_rate": data.get("mean_seeded_bug_catch_rate"),
        })
    out.sort(key=lambda b: b["mtime"], reverse=True)
    return out


def _default_llm_factory() -> LLM:
    return LLM(AppConfig())


class _CachingStatic(StaticFiles):
    """Serves static assets with a long-cache header. Safe because we
    version-bust via a ``?v=`` query string in the templates."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        resp = await super().get_response(path, scope)
        if getattr(resp, "status_code", 500) == 200:
            resp.headers.setdefault("Cache-Control", "public, max-age=86400, immutable")
        return resp


def create_app(
    runs_root: Path | None = None,
    *,
    llm_factory: Callable[[], LLM] = _default_llm_factory,
    jobs: JobRegistry | None = None,
) -> FastAPI:
    """Build the FastAPI app rooted at ``runs_root``.

    ``runs_root=None`` reads ``MUTAGEN_RUNS_ROOT`` from env (default
    ``./runs``). Passing an explicit path is how tests + the CLI wire
    a specific location. Injecting ``jobs`` lets tests preload a
    registry with a ``FakeLLM`` factory.

    Being callable with no args is what makes ``uvicorn --factory
    mutagen.web:create_app`` work in the deployment Dockerfile.
    """
    if runs_root is None:
        import os
        runs_root = Path(os.environ.get("MUTAGEN_RUNS_ROOT", "./runs"))
    runs_root = runs_root.resolve()
    app = FastAPI(title="mutagen dashboard", docs_url=None, redoc_url=None)
    app.state.jobs = jobs or JobRegistry(runs_root, llm_factory=llm_factory)
    app.state.started_at = time.time()

    templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
    templates.env.globals["fmt_pct"] = lambda x: f"{x * 100:.1f}%" if x is not None else "-"
    templates.env.globals["fmt_num"] = lambda x: f"{x:,}" if x is not None else "-"
    templates.env.globals["auth_configured"] = is_auth_configured

    if _STATIC_DIR.is_dir():
        app.mount("/static", _CachingStatic(directory=str(_STATIC_DIR)), name="static")

    def _resolve_run(name: str) -> Path:
        """Reject traversal (``..``) and return the canonical run folder."""
        p = (runs_root / name).resolve()
        if runs_root not in p.parents and p != runs_root:
            raise HTTPException(status_code=400, detail="path escapes runs root")
        if not p.is_dir():
            raise HTTPException(status_code=404, detail=f"run {name!r} not found")
        return p

    @app.get("/", response_class=HTMLResponse)
    def landing(request: Request) -> HTMLResponse:
        """Landing page is the new-run form -- most users want to
        start something, not browse history. The runs list lives at
        ``/runs``."""
        return templates.TemplateResponse(request, "new_run.html", {"landing": True})

    @app.get("/runs", response_class=HTMLResponse)
    def runs_index(request: Request) -> HTMLResponse:
        runs = _scan_runs(runs_root)
        benches = _scan_benchmarks(runs_root)
        return templates.TemplateResponse(
            request, "runs_list.html",
            {"runs": runs, "benches": benches, "runs_root": runs_root},
        )

    @app.get("/runs/{name}", response_class=HTMLResponse)
    def run_detail(request: Request, name: str) -> HTMLResponse:
        run_dir = _resolve_run(name)
        run_json = _load_json(run_dir / "run.json")
        round_files = sorted(run_dir.glob("round_*_report.json"))
        rounds = []
        for rf in round_files:
            data = _load_json(rf)
            if data is not None:
                rounds.append(data)
        # If run.json is present, prefer its rounds (has full to_dict shape).
        if run_json and run_json.get("rounds"):
            rounds = run_json["rounds"]

        target_py = run_dir / "target.py"
        target_src = target_py.read_text(encoding="utf-8") if target_py.is_file() else ""

        # Optional plain-English focus the user set at submit time. Read from
        # disk (not from the Job registry) so it survives process restarts.
        focus_path = run_dir / "focus.txt"
        focus = focus_path.read_text(encoding="utf-8").strip() if focus_path.is_file() else None

        # Chart data: kill rate per round.
        chart_labels = [f"R{r.get('index', i + 1)}" for i, r in enumerate(rounds)]
        chart_kill = [
            (r.get("report") or {}).get("kill_rate", 0.0) * 100
            for r in rounds
        ]
        return templates.TemplateResponse(
            request, "run_detail.html",
            {
                "name": name,
                "run": run_json,
                "rounds": rounds,
                "target_src": target_src,
                "focus": focus,
                "chart_labels": chart_labels,
                "chart_kill": chart_kill,
            },
        )

    @app.get("/runs/{name}/tests/{round_idx}", response_class=PlainTextResponse)
    def run_tests(name: str, round_idx: int) -> PlainTextResponse:
        run_dir = _resolve_run(name)
        p = run_dir / f"test_round_{round_idx}.py"
        if not p.is_file():
            raise HTTPException(status_code=404, detail="tests file not found")
        return PlainTextResponse(p.read_text(encoding="utf-8"))

    @app.get("/runs/{name}/debrief/{round_idx}", response_class=HTMLResponse)
    def run_debrief(request: Request, name: str, round_idx: int) -> HTMLResponse:
        """Render ``round_N_debrief.md`` as a stand-alone HTML page.

        Serving the raw markdown would work too, but browsers render it as
        plain text, and the whole point of the file is for humans to read.
        """
        run_dir = _resolve_run(name)
        md_path = run_dir / f"round_{round_idx}_debrief.md"
        if not md_path.is_file():
            raise HTTPException(status_code=404, detail="debrief not found")
        md_text = md_path.read_text(encoding="utf-8")
        return templates.TemplateResponse(
            request, "debrief.html",
            {
                "name": name,
                "round_idx": round_idx,
                "markdown": md_text,
                "rendered": render_markdown(md_text),
            },
        )

    @app.get("/runs/{name}/debrief/{round_idx}/raw", response_class=PlainTextResponse)
    def run_debrief_raw(name: str, round_idx: int) -> PlainTextResponse:
        """Raw markdown, for copy/paste into a PR body or a bug report."""
        run_dir = _resolve_run(name)
        md_path = run_dir / f"round_{round_idx}_debrief.md"
        if not md_path.is_file():
            raise HTTPException(status_code=404, detail="debrief not found")
        return PlainTextResponse(md_path.read_text(encoding="utf-8"))

    @app.get("/api/runs/{name}", response_class=JSONResponse)
    def api_run(name: str) -> JSONResponse:
        run_dir = _resolve_run(name)
        data = _load_json(run_dir / "run.json")
        if data is None:
            raise HTTPException(status_code=404, detail="run.json not found")
        return JSONResponse(data)

    # -- LLM-driven post-run report (on-demand) --------------------------

    def _final_mutation_report(run_dir: Path) -> tuple[MutationReport, str] | None:
        """Reconstruct the final MutationReport from ``run.json`` + the last
        round's report; also return a display target name."""
        run_json = _load_json(run_dir / "run.json")
        if run_json is None:
            return None
        rounds = run_json.get("rounds") or []
        if not rounds:
            return None
        # The final round's report is the authoritative kill-rate snapshot.
        final_rd = rounds[-1].get("report")
        if not final_rd:
            return None
        survivors = [
            Mutant(
                id=m.get("id", ""),
                file=m.get("file"),
                line=m.get("line"),
                status=m.get("status", "survived"),
                diff=m.get("diff"),
                kind=m.get("kind", "other"),
            )
            for m in (final_rd.get("survivors") or [])
        ]
        report = MutationReport(
            total=final_rd.get("total", 0),
            killed=final_rd.get("killed", 0),
            survived=final_rd.get("survived", 0),
            timeout=final_rd.get("timeout", 0),
            suspicious=final_rd.get("suspicious", 0),
            skipped=final_rd.get("skipped", 0),
            survivors=survivors,
            disabled_types=final_rd.get("disabled_types", []),
        )
        # target_name is the workdir slug minus the trailing ``-<id[:8]>``.
        target_name = run_dir.name.rsplit("-", 1)[0] or run_dir.name
        return report, target_name

    def _report_state(run_dir: Path) -> str:
        """One of ``ready`` (analysis.json exists), ``pending`` (marker file), ``missing``."""
        if (run_dir / "analysis.json").is_file():
            return "ready"
        if (run_dir / "analysis.pending").is_file():
            return "pending"
        return "missing"

    def _load_analysis(run_dir: Path) -> AnalysisReport | None:
        raw = _load_json(run_dir / "analysis.json")
        if raw is None:
            return None
        try:
            return AnalysisReport.from_dict(raw)
        except (KeyError, ValueError, TypeError):
            return None

    def _generate_analysis_in_background(run_dir: Path) -> None:
        """Runs off the request thread. Any failure leaves ``analysis.error``
        behind and clears the pending marker so the UI can surface it."""
        try:
            got = _final_mutation_report(run_dir)
            if got is None:
                raise RuntimeError(
                    "run has no final round report -- cannot analyze until a run completes"
                )
            report, target_name = got
            llm = llm_factory()
            # Prefer the model configured on the LLM instance; fall back to a
            # fresh AppConfig for LLMs that don't carry cfg (FakeLLM in tests).
            cfg_model = getattr(getattr(llm, "_cfg", None), "llm", None)
            model_name = (
                cfg_model.analysis.model if cfg_model else AppConfig().llm.analysis.model
            )
            analysis = analyze_run(
                run_dir,
                llm=llm,
                report=report,
                target_name=target_name,
                model_name=model_name,
            )
            (run_dir / "analysis.json").write_text(
                json.dumps(analysis.to_dict(), indent=2, default=str), encoding="utf-8",
            )
            # Clear any prior error file if this succeeded.
            err = run_dir / "analysis.error"
            if err.is_file():
                err.unlink()
        except Exception as e:  # noqa: BLE001 -- surface to UI, don't crash server
            (run_dir / "analysis.error").write_text(
                f"{type(e).__name__}: {e}", encoding="utf-8",
            )
        finally:
            pending = run_dir / "analysis.pending"
            if pending.is_file():
                pending.unlink()

    def _render_report_html(request: Request, run_dir: Path, analysis: AnalysisReport) -> str:
        """Render the report template to a standalone HTML string (used by
        both the browser view and the PDF exporter)."""
        return templates.get_template("report.html").render(
            {
                "request": request,
                "analysis": analysis,
                "severity_counts": analysis.severity_counts(),
                "sorted_survivors": analysis.sorted_survivors(),
                "generated_at_pretty": datetime.fromtimestamp(
                    analysis.generated_at, tz=timezone.utc,
                ).strftime("%Y-%m-%d %H:%M UTC"),
                "fmt_pct": lambda x: f"{x * 100:.1f}%" if x is not None else "-",
                "fmt_num": lambda x: f"{x:,}" if x is not None else "-",
                "auth_configured": is_auth_configured,
            }
        )

    @app.get("/runs/{name}/report", response_class=HTMLResponse)
    def run_report(request: Request, name: str) -> HTMLResponse:
        run_dir = _resolve_run(name)
        state = _report_state(run_dir)
        analysis = _load_analysis(run_dir) if state == "ready" else None
        if analysis is not None:
            return HTMLResponse(_render_report_html(request, run_dir, analysis))
        # No analysis yet -- show the "generate" placeholder or a spinner.
        err_path = run_dir / "analysis.error"
        error_msg = err_path.read_text(encoding="utf-8") if err_path.is_file() else None
        run_json = _load_json(run_dir / "run.json")
        return templates.TemplateResponse(
            request, "report_pending.html",
            {
                "name": name,
                "state": state,
                "error": error_msg,
                "has_run_json": run_json is not None,
            },
        )

    @app.post(
        "/api/runs/{name}/report/generate",
        dependencies=[Depends(require_write_auth)],
    )
    def generate_report(name: str, background_tasks: BackgroundTasks) -> JSONResponse:
        run_dir = _resolve_run(name)
        state = _report_state(run_dir)
        if state == "pending":
            return JSONResponse({"state": "pending"}, status_code=202)
        if state == "ready":
            return JSONResponse({"state": "ready"}, status_code=200)
        if _final_mutation_report(run_dir) is None:
            raise HTTPException(
                status_code=409,
                detail="run has not finished yet (no final round report to analyze)",
            )
        (run_dir / "analysis.pending").write_text(
            str(time.time()), encoding="utf-8",
        )
        # Kick off outside the request thread so the LLM latency doesn't hold
        # the HTTP response open. BackgroundTasks fires after the response.
        background_tasks.add_task(_generate_analysis_in_background, run_dir)
        return JSONResponse({"state": "pending"}, status_code=202)

    @app.get("/api/runs/{name}/report/status")
    def report_status(name: str) -> JSONResponse:
        run_dir = _resolve_run(name)
        state = _report_state(run_dir)
        payload: dict = {"state": state}
        err_path = run_dir / "analysis.error"
        if err_path.is_file():
            payload["error"] = err_path.read_text(encoding="utf-8")
        return JSONResponse(payload)

    @app.get("/runs/{name}/report.pdf")
    def run_report_pdf(request: Request, name: str) -> Response:
        run_dir = _resolve_run(name)
        analysis = _load_analysis(run_dir)
        if analysis is None:
            raise HTTPException(
                status_code=404,
                detail="analysis.json not found -- generate the report first",
            )
        html = _render_report_html(request, run_dir, analysis)
        try:
            pdf_bytes = html_to_pdf(html)
        except Exception as e:  # noqa: BLE001 -- surface as 500
            raise HTTPException(status_code=500, detail=f"pdf render failed: {e}") from e
        filename = f"{name}_report.pdf"
        return Response(
            pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/bench/{name}", response_class=HTMLResponse)
    def bench_detail(request: Request, name: str) -> HTMLResponse:
        run_dir = _resolve_run(name)
        data = _load_json(run_dir / "benchmark.json")
        if data is None:
            raise HTTPException(status_code=404, detail="benchmark.json not found")
        return templates.TemplateResponse(
            request, "bench_detail.html",
            {"name": name, "bench": data, "entries": data.get("entries", [])},
        )

    # -- jobs (write endpoints, opt-in-auth-gated) -----------------------

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_list(request: Request) -> HTMLResponse:
        js = [j.to_dict() for j in app.state.jobs.list()]
        return templates.TemplateResponse(request, "jobs_list.html", {"jobs": js})

    @app.get("/new")
    def new_run_form_redirect() -> RedirectResponse:
        """Back-compat: the form now lives at ``/``. 308 preserves the
        method so anything scripted against POST /new still works."""
        return RedirectResponse("/", status_code=308)

    @app.post("/jobs", dependencies=[Depends(require_write_auth)], response_model=None)
    async def submit_job(
        request: Request,
        target_name: str = Form(...),
        max_rounds: int = Form(3),
        target_source: str | None = Form(None),
        repo_url: str | None = Form(None),
        repo_target_path: str | None = Form(None),
        focus: str | None = Form(None),
    ) -> RedirectResponse | JSONResponse:
        try:
            job = app.state.jobs.submit(
                target_name=target_name,
                max_rounds=max_rounds,
                # Empty strings from HTML forms become None so the
                # exactly-one-of check in submit() reads cleanly.
                target_source=target_source or None,
                repo_url=repo_url or None,
                repo_target_path=repo_target_path or None,
                focus=focus or None,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        # Content negotiation: JSON callers get JSON, browsers get a redirect.
        accept = request.headers.get("accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return JSONResponse(job.to_dict(), status_code=201)
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: str) -> HTMLResponse:
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return templates.TemplateResponse(
            request, "job_detail.html",
            {"job": job.to_dict()},
        )

    @app.get("/api/jobs")
    def api_jobs() -> JSONResponse:
        return JSONResponse([j.to_dict() for j in app.state.jobs.list()])

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str) -> JSONResponse:
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return JSONResponse(job.to_dict())

    @app.delete("/api/jobs/{job_id}", dependencies=[Depends(require_write_auth)])
    def cancel_job(job_id: str) -> JSONResponse:
        ok = app.state.jobs.cancel(job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="job not found or already terminal")
        return JSONResponse({"cancelled": True})

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str) -> StreamingResponse:
        """SSE stream: one event per round-completion plus a terminal event.

        We drain the job's queue in a background thread and yield events into
        the async generator via a loop-safe run_in_executor call. Clients that
        disconnect can reconnect and get any events still in the buffer; the
        rest is on disk under ``/runs/<workdir>``.
        """
        job = app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        async def stream():
            loop = asyncio.get_running_loop()
            # Emit an initial snapshot so the client renders something immediately.
            yield f"event: snapshot\ndata: {json.dumps(job.to_dict())}\n\n"
            while True:
                event = await loop.run_in_executor(None, job.events.get)
                if event.get("type") == "end":
                    yield "event: end\ndata: {}\n\n"
                    return
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- ops -------------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({
            "status": "ok",
            "uptime_s": int(time.time() - app.state.started_at),
            "auth": "configured" if is_auth_configured() else "open",
            "runs_root": str(runs_root),
            "jobs_in_registry": len(app.state.jobs.list()),
        })

    return app
