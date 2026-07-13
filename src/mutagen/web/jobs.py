"""In-memory job registry with a threaded runner.

Design constraints (deliberate):
    - No external dependency (Redis / Celery). Threads + a process-local
      registry are enough for the deployment shape we care about right
      now (single-node behind a reverse proxy).
    - Jobs write their artifacts into the same ``runs/`` folder the
      read-only dashboard scans, so a completed job is immediately
      viewable via the existing pages -- no separate storage layer.
    - Round-completion events are pushed into a per-job ``queue.Queue``
      so the SSE endpoint can drain without polling the filesystem.

Assumptions we're accepting for now:
    - Not restart-safe. Killing the process kills in-flight jobs; the
      artifacts on disk survive, but ``running`` jobs become
      indistinguishable from ``crashed``. Fine for a small deployment;
      swap to Celery + Redis when this hurts.
    - Not multi-worker. Two uvicorn workers would each have their own
      registry. Deploy single-worker; scale by putting mutagen behind a
      load balancer with sticky sessions if that ever becomes real.
    - No hard-cancel: the loop is not preemptible mid-round. Cancel
      requests are honored between rounds (checked via
      ``Job.cancel_requested``).
"""

from __future__ import annotations

import queue
import shutil
import sqlite3
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Poll cadence for the mutmut progress watcher. Small enough to feel live in
# the UI, large enough that the sqlite reads don't compete with mutmut's own
# writes on the same file.
_MUTMUT_POLL_S = 3.0

from typing import Literal

from mutagen.agent.llm import LLM
from mutagen.agent.loop import LoopResult, run_loop
from mutagen.config import AppConfig
from mutagen.repo import CloneError, clone_repo, detect_languages

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]

# Per-job event queue capacity. Round events are cheap (a small dict), so
# this is generous -- clients that disconnect and reconnect will still get
# a full replay from the on-disk artifacts, but the live stream buffers
# what they missed during the reconnect.
_EVENT_QUEUE_MAX = 128

# Sentinel enqueued when a job terminates so the SSE loop can exit cleanly.
_END_OF_STREAM: dict = {"type": "end"}


@dataclass
class Job:
    id: str
    target_name: str            # user-supplied display name
    workdir: Path               # <runs_root>/<name-slug>-<id[:8]>
    max_rounds: int
    status: JobStatus = "queued"
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    # Not preemptible mid-round; loop checks between rounds.
    cancel_requested: bool = False
    # Populated as rounds complete; also mirrored to disk via run.json.
    rounds_done: int = 0
    final_kill_rate: float | None = None
    # Bounded queue drained by the SSE endpoint.
    events: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=_EVENT_QUEUE_MAX))
    # Repo-mode extras (None for paste-source jobs). Populated after clone.
    repo_url: str | None = None
    repo_target_path: str | None = None
    languages: dict[str, int] = field(default_factory=dict)
    # Optional plain-English description of what the user wants tested.
    # Persisted to ``workdir/focus.txt`` at run start so downstream modules
    # (codegen prompts, report analysis) can read it without threading a
    # parameter through every layer.
    focus: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_name": self.target_name,
            "workdir": str(self.workdir),
            "workdir_name": self.workdir.name,
            "max_rounds": self.max_rounds,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "rounds_done": self.rounds_done,
            "final_kill_rate": self.final_kill_rate,
            "repo_url": self.repo_url,
            "repo_target_path": self.repo_target_path,
            "languages": self.languages,
            "focus": self.focus,
        }


def _slug(name: str) -> str:
    """Filesystem-safe short slug from a display name."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.strip())
    safe = safe.strip("-") or "job"
    return safe[:40].lower()


class JobRegistry:
    """Thread-safe in-process job store.

    ``llm_factory`` returns a fresh ``LLM`` per job so per-job token
    accounting is isolated; tests inject a ``FakeLLM`` factory to avoid
    real API calls. ``loop_runner`` is the run_loop callable (parameterized
    so tests can substitute a fast stub without monkeypatching a module).
    """

    def __init__(
        self,
        runs_root: Path,
        *,
        llm_factory: Callable[[], LLM],
        loop_runner: Callable[..., LoopResult] = run_loop,
    ) -> None:
        self._runs_root = runs_root.resolve()
        self._runs_root.mkdir(parents=True, exist_ok=True)
        self._llm_factory = llm_factory
        self._loop_runner = loop_runner
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    # -- lifecycle -------------------------------------------------------

    def submit(
        self,
        *,
        target_name: str,
        max_rounds: int,
        target_source: str | None = None,
        repo_url: str | None = None,
        repo_target_path: str | None = None,
        focus: str | None = None,
    ) -> Job:
        """Create workdir, register a job, and spawn the runner.

        Two modes, exclusive:
          - **paste**: ``target_source`` is the raw Python module.
          - **repo**: ``repo_url`` is cloned into ``workdir/_repo/``; the
            actual target inside the repo is ``repo_target_path`` (relative,
            must resolve inside the clone).

        Clone I/O runs in the worker thread, not here, so the HTTP submit
        returns instantly and the user sees a "cloning..." event on the SSE
        stream.
        """
        if max_rounds < 1 or max_rounds > 10:
            raise ValueError("max_rounds must be in [1, 10]")
        if (target_source is None) == (repo_url is None):
            raise ValueError("provide exactly one of target_source or repo_url")
        if repo_url is not None and not (repo_target_path or "").strip():
            raise ValueError("repo_target_path is required when repo_url is set")
        if target_source is not None and not target_source.strip():
            raise ValueError("target_source is empty")

        job_id = uuid.uuid4().hex
        slug = _slug(target_name)
        wd = self._runs_root / f"{slug}-{job_id[:8]}"
        wd.mkdir(parents=True, exist_ok=True)

        if target_source is not None:
            # Sidecar name avoids SameFileError when the loop copies target
            # into ``workdir/target.py`` (Windows raises on self-copy).
            (wd / "_input.py").write_text(target_source, encoding="utf-8")

        # Store focus on the job AND drop it into the workdir so downstream
        # modules (codegen, report analysis) can pick it up without wiring
        # a new parameter through the whole call chain.
        focus_clean = (focus or "").strip() or None
        if focus_clean:
            (wd / "focus.txt").write_text(focus_clean, encoding="utf-8")

        job = Job(
            id=job_id, target_name=target_name, workdir=wd, max_rounds=max_rounds,
            repo_url=repo_url,
            repo_target_path=(repo_target_path or None) if repo_url else None,
            focus=focus_clean,
        )
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(
            target=self._run, args=(job,), name=f"mutagen-job-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job

    def _prepare_repo_target(self, job: Job) -> Path:
        """Clone the repo, detect languages, resolve the requested target
        file safely, and return the path the loop should test."""
        self._emit(job, {"type": "cloning", "url": job.repo_url})
        clone_dir = job.workdir / "_repo"
        clone_repo(job.repo_url, clone_dir)  # raises CloneError

        # Language census -- useful signal for the UI even in Python-only mode.
        job.languages = detect_languages(clone_dir)
        self._emit(job, {"type": "languages", "languages": job.languages})

        # Resolve target safely: must stay inside clone_dir, must exist,
        # must be a .py for the current Python-only pipeline.
        rel = (job.repo_target_path or "").strip().lstrip("/\\")
        candidate = (clone_dir / rel).resolve()
        if clone_dir.resolve() not in candidate.parents and candidate != clone_dir.resolve():
            raise RuntimeError(f"target path escapes repo root: {rel!r}")
        if not candidate.is_file():
            raise RuntimeError(f"target file not found in repo: {rel!r}")
        if candidate.suffix != ".py":
            raise RuntimeError(
                f"only Python targets are supported today; got {candidate.suffix!r}. "
                "JS/TS/Java/C#/C++ are on the roadmap."
            )
        # Copy into the sidecar so downstream _run() code path is uniform
        # regardless of mode. Loop's _prepare_workdir will then copy the
        # sidecar into workdir/target.py.
        sidecar = job.workdir / "_input.py"
        sidecar.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")

        # Pin pytest's rootdir + testpaths to the workdir; otherwise a
        # cloned repo containing 100+ .py files under ``_repo/`` will cause
        # pytest to auto-discover across the whole tree during mutmut,
        # multiplying wall clock by 10-100x.
        (job.workdir / "pytest.ini").write_text(
            "[pytest]\n"
            "testpaths = .\n"
            "norecursedirs = _repo __pycache__ .mutmut-cache\n"
            "rootdir = .\n",
            encoding="utf-8",
        )

        self._emit(job, {"type": "target_selected", "path": rel})
        return sidecar

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        """Newest-first snapshot."""
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def cancel(self, job_id: str) -> bool:
        """Ask the runner to stop between rounds. Returns True if the job
        exists and wasn't already terminal."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.status not in ("queued", "running"):
                return False
            job.cancel_requested = True
        return True

    # -- runner ---------------------------------------------------------

    def _read_mutmut_counts(self, workdir: Path) -> dict | None:
        """Return current killed/survived/untested counts from mutmut's cache,
        or None if the cache isn't readable yet.

        mutmut writes to ``.mutmut-cache`` as it processes each mutant, so a
        cheap read-only query gives a live progress signal without touching
        mutmut itself.
        """
        cache = workdir / ".mutmut-cache"
        if not cache.is_file():
            return None
        try:
            # ``mode=ro`` + short timeout so we never block mutmut's writes.
            conn = sqlite3.connect(
                f"file:{cache.as_posix()}?mode=ro", uri=True, timeout=0.5,
            )
            try:
                rows = conn.execute(
                    "SELECT status, COUNT(*) FROM Mutant GROUP BY status"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        counts = dict(rows)
        killed = counts.get("ok_killed", 0)
        survived = counts.get("bad_survived", 0)
        untested = counts.get("untested", 0)
        total = sum(counts.values())
        return {
            "killed": killed,
            "survived": survived,
            "untested": untested,
            "total": total,
        }

    def _emit(self, job: Job, event: dict) -> None:
        """Non-blocking put; drop the oldest event on backpressure so a
        stuck SSE client can't wedge the runner thread."""
        try:
            job.events.put_nowait(event)
        except queue.Full:
            try:
                job.events.get_nowait()
            except queue.Empty:
                pass
            try:
                job.events.put_nowait(event)
            except queue.Full:  # pragma: no cover -- shouldn't happen
                pass

    def _run(self, job: Job) -> None:
        job.status = "running"
        job.started_at = time.monotonic()
        self._emit(job, {"type": "started", "job_id": job.id})

        try:
            if job.repo_url:
                target_path = self._prepare_repo_target(job)
            else:
                target_path = job.workdir / "_input.py"
            llm = self._llm_factory()
            cfg = AppConfig()

            # We wrap run_loop with per-round + per-mutmut observers by
            # monkey-patching two loop-module hooks. The persist patch
            # fires at each round boundary; the mutmut patch spawns a
            # short-lived progress poller so the UI shows live counts
            # while mutmut grinds through mutants (often 2-5 minutes on
            # Windows, which otherwise looks frozen).
            from mutagen.agent import loop as loop_mod
            original_persist = loop_mod._persist_round
            original_mutmut = loop_mod.run_mutmut

            def _observed_persist(workdir: Path, r) -> None:
                original_persist(workdir, r)
                job.rounds_done = r.index
                self._emit(job, {
                    "type": "round",
                    "index": r.index,
                    "tier": r.tier,
                    "pytest_ok": r.pytest_ok,
                    "repaired": r.repaired,
                    "kill_rate": r.report.kill_rate if r.report else None,
                    "killed": r.report.killed if r.report else None,
                    "total": r.report.total if r.report else None,
                    "survived": r.report.survived if r.report else None,
                    "elapsed_s": r.elapsed_s,
                })
                if job.cancel_requested:
                    # This is the honest cancel signal -- surfaces through
                    # the loop's next round-boundary check via an exception.
                    raise _CancelRequested()

            def _observed_mutmut(**kwargs):
                workdir = kwargs.get("workdir")
                self._emit(job, {"type": "mutmut_started", "workdir": str(workdir)})
                stop = threading.Event()
                started_at = time.monotonic()

                def poll():
                    while not stop.wait(_MUTMUT_POLL_S):
                        counts = self._read_mutmut_counts(workdir)
                        if counts is None:
                            continue
                        self._emit(job, {
                            "type": "mutmut_progress",
                            "elapsed_s": time.monotonic() - started_at,
                            **counts,
                        })

                watcher = threading.Thread(
                    target=poll, daemon=True,
                    name=f"mutmut-watcher-{job.id[:8]}",
                )
                watcher.start()
                try:
                    return original_mutmut(**kwargs)
                finally:
                    stop.set()
                    # One final snapshot so the UI's last number matches disk.
                    counts = self._read_mutmut_counts(workdir)
                    if counts is not None:
                        self._emit(job, {
                            "type": "mutmut_progress",
                            "elapsed_s": time.monotonic() - started_at,
                            **counts,
                        })
                    self._emit(job, {"type": "mutmut_done"})

            loop_mod._persist_round = _observed_persist
            loop_mod.run_mutmut = _observed_mutmut
            try:
                result = self._loop_runner(
                    target=target_path, workdir=job.workdir, cfg=cfg, llm=llm,
                    max_rounds=job.max_rounds,
                )
            finally:
                loop_mod._persist_round = original_persist
                loop_mod.run_mutmut = original_mutmut

            if result.final_report is not None:
                job.final_kill_rate = result.final_report.kill_rate
            job.status = "done"
            self._emit(job, {
                "type": "done",
                "final_kill_rate": job.final_kill_rate,
                "stopped_reason": result.stopped_reason,
                "rounds": len(result.rounds),
            })
        except _CancelRequested:
            job.status = "cancelled"
            self._emit(job, {"type": "cancelled"})
        except Exception as e:  # noqa: BLE001 -- surface loop crashes cleanly
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            self._emit(job, {"type": "failed", "error": job.error,
                              "traceback": traceback.format_exc()[-2000:]})
        finally:
            job.finished_at = time.monotonic()
            # Drop the cloned repo once the job is terminal -- it's ~2-3 MB
            # per run and only useful while pytest is being invoked. Debriefs,
            # mutants, and pytest artifacts under the workdir survive.
            if job.repo_url:
                shutil.rmtree(job.workdir / "_repo", ignore_errors=True)
            self._emit(job, _END_OF_STREAM)


class _CancelRequested(RuntimeError):
    """Raised inside the observer hook when a user asked us to stop."""
