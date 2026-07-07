"""Isolated execution of generated tests.

Responsibility: run pytest on generated tests against the target without
letting bad/malicious/looping test code affect the host. Pluggable backends:

    subprocess  -- resource-limited local process (default; Windows-friendly).
    docker      -- container-based isolation (optional; stronger boundary).
"""
