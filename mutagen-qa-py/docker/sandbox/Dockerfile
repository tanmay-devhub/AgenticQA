# Minimal sandbox image for running the generated pytest suite + mutmut
# against an untrusted target. Runs as an unprivileged user with the workdir
# mounted read-write at /work.
FROM python:3.12-slim

RUN adduser --disabled-password --gecos "" --uid 10001 sandbox \
    && mkdir -p /work \
    && chown sandbox:sandbox /work

# Everything the loop needs at runtime. Pinned lightly -- we only care that
# the surface matches the host, not that the versions are locked forever.
RUN pip install --no-cache-dir \
        "pytest>=8.0" \
        "pytest-cov>=5.0" \
        "coverage>=7.4" \
        "mutmut>=2.5,<3.0" \
        "hypothesis>=6.100"

USER sandbox
WORKDIR /work

# Force UTF-8 I/O so mutmut's emoji doesn't crash on non-UTF8 locales
# (same fix we apply on the host subprocess backend).
ENV PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8
