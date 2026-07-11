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
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from mutagen.agent.llm import LLM
from mutagen.agent.loop import LoopResult, run_loop
from mutagen.config import AppConfig

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

    def submit(self, *, target_source: str, target_name: str, max_rounds: int) -> Job:
        """Create workdir, persist target.py, register job, spawn runner."""
        if not target_source.strip():
            raise ValueError("target_source is empty")
        if max_rounds < 1 or max_rounds > 10:
            raise ValueError("max_rounds must be in [1, 10]")
        job_id = uuid.uuid4().hex
        slug = _slug(target_name)
        wd = self._runs_root / f"{slug}-{job_id[:8]}"
        wd.mkdir(parents=True, exist_ok=True)
        # Write user-supplied source to a sidecar path -- ``run_loop`` will
        # copy it to ``workdir/target.py`` during ``_prepare_workdir``, and
        # a self-copy raises SameFileError on Windows.
        (wd / "_input.py").write_text(target_source, encoding="utf-8")

        job = Job(id=job_id, target_name=target_name, workdir=wd, max_rounds=max_rounds)
        with self._lock:
            self._jobs[job_id] = job

        thread = threading.Thread(
            target=self._run, args=(job,), name=f"mutagen-job-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job

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

        target_path = job.workdir / "_input.py"
        try:
            llm = self._llm_factory()
            cfg = AppConfig()

            # We wrap run_loop with a per-round observer by monkey-patching
            # the persistence hook -- the loop already calls _persist_round
            # after each RoundResult, which is our natural pulse.
            from mutagen.agent import loop as loop_mod
            original_persist = loop_mod._persist_round

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

            loop_mod._persist_round = _observed_persist
            try:
                result = self._loop_runner(
                    target=target_path, workdir=job.workdir, cfg=cfg, llm=llm,
                    max_rounds=job.max_rounds,
                )
            finally:
                loop_mod._persist_round = original_persist

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
            self._emit(job, _END_OF_STREAM)


class _CancelRequested(RuntimeError):
    """Raised inside the observer hook when a user asked us to stop."""
