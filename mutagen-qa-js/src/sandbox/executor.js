/**
 * child_process wrapper. Mirrors mutagen-qa/src/mutagen/sandbox/executor.py:
 * one entry point that returns a RunResult { returncode, stdout, stderr,
 * timedOut } so the loop doesn't care whether it was a subprocess or a
 * container.
 *
 * Docker backend is a stub for now -- the JS pipeline can add it later
 * without changing callers.
 */

import { spawn } from "node:child_process";

export function runSubprocess(argv, { cwd, timeoutS = 30, env } = {}) {
  return new Promise((resolve) => {
    const child = spawn(argv[0], argv.slice(1), {
      cwd,
      env: { ...process.env, ...(env || {}) },
      shell: false,
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      try {
        child.kill("SIGKILL");
      } catch {
        /* already dead */
      }
    }, timeoutS * 1000);

    child.stdout.on("data", (d) => {
      stdout += d.toString("utf8");
    });
    child.stderr.on("data", (d) => {
      stderr += d.toString("utf8");
    });
    child.on("error", (err) => {
      clearTimeout(timer);
      resolve({ returncode: -1, stdout, stderr: stderr + `\n[spawn error] ${err.message}`, timedOut });
    });
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      const rc = typeof code === "number" ? code : signal ? 128 : -1;
      resolve({ returncode: rc, stdout, stderr, timedOut });
    });
  });
}

// Docker backend (mirrors Python's sandbox/docker.py) is not shipped in the
// JS pipeline yet. Callers use runSubprocess directly; when the container
// path lands, add a run(argv, {backend}) dispatcher here.
