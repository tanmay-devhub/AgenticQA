# Web deployment image for mutagen-qa.
#
# Serves the FastAPI dashboard + jobs API on :8000 with uvicorn. Mount a
# writable ``runs/`` volume at /data/runs to persist artifacts across
# container restarts; jobs land there so the read-only dashboard picks
# them up immediately.
#
# Auth is opt-in: set MUTAGEN_WEB_AUTH_TOKEN=<token> to require a bearer
# token on write endpoints (POST /jobs, DELETE /api/jobs/{id}). Read
# endpoints stay open for viewers.
#
# LLM keys are passed via env at runtime (OLLAMA_API_KEY, GEMINI_API_KEY,
# or MUTAGEN_{CODEGEN,PLANNER}_MODEL for provider swaps).
FROM python:3.12-slim AS base

RUN adduser --disabled-password --gecos "" --uid 10001 mutagen \
    && mkdir -p /data/runs \
    && chown -R mutagen:mutagen /data

WORKDIR /app

# Layer 1: dependency install. Copy only what pip needs so app-source changes
# don't invalidate the wheel cache.
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir "uvicorn[standard]>=0.29"

USER mutagen

ENV MUTAGEN_RUNS_ROOT=/data/runs \
    PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

# One uvicorn worker on purpose -- the in-memory JobRegistry is per-process.
# Scale horizontally behind a load balancer with sticky sessions, or swap the
# registry for Celery + Redis if that's not enough.
CMD ["python", "-m", "uvicorn", "mutagen.web:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--log-level", "warning"]
