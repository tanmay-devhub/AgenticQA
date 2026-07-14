/**
 * Shallow git-clone helper. JS port of mutagen-qa/src/mutagen/repo/clone.py.
 *
 * Contract:
 *   - Only https:// / http:// / file:// URLs are accepted. SSH and git:// are
 *     rejected (SSH needs agent forwarding in server deployments;
 *     unauthenticated git:// is deprecated).
 *   - depth = 1 by default (we generate tests from source, not history).
 *   - Timeout is enforced -- a slow remote can't wedge a job.
 *   - Destination must not exist as a non-empty directory.
 *
 * Failures throw CloneError so callers can surface a clean message instead
 * of a raw traceback.
 */

import { existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { runSubprocess } from "../sandbox/executor.js";

const SCHEME_ALLOW = ["https://", "http://", "file://"];
const URL_SANITY_RE = /^[A-Za-z][A-Za-z0-9+.-]*:\/\/[^\s]+$/;

export class CloneError extends Error {
  constructor(message) {
    super(message);
    this.name = "CloneError";
  }
}

function validateUrl(url) {
  if (typeof url !== "string" || !url.trim()) throw new CloneError("repo URL is empty");
  if (!URL_SANITY_RE.test(url)) throw new CloneError(`repo URL is not a valid URL: ${JSON.stringify(url)}`);
  if (!SCHEME_ALLOW.some((s) => url.startsWith(s))) {
    throw new CloneError(
      `repo URL must use https:// (or file:// for local testing); got ${JSON.stringify(url)}`,
    );
  }
}

function validateDest(dest) {
  if (existsSync(dest)) {
    const s = statSync(dest);
    if (!s.isDirectory()) throw new CloneError(`destination exists and is not a directory: ${dest}`);
    if (readdirSync(dest).length > 0) throw new CloneError(`destination is not empty: ${dest}`);
  }
}

export async function cloneRepo(url, dest, { depth = 1, timeoutS = 120 } = {}) {
  validateUrl(url);
  const resolved = path.resolve(dest);
  validateDest(resolved);
  mkdirSync(resolved, { recursive: true });

  const argv = ["git", "clone", "--depth", String(depth), "--single-branch", url, resolved];
  const res = await runSubprocess(argv, { timeoutS });
  if (res.timedOut) throw new CloneError(`git clone timed out after ${timeoutS}s`);
  if (res.returncode !== 0) {
    // Windows: EINVAL surfaces here when `git` is a package-manager .cmd shim
    // rather than a real binary. runSubprocess reports it via stderr, but the
    // message is opaque -- surface a clearer hint.
    const stderr = res.stderr || "";
    if (/spawn error.*EINVAL/i.test(stderr) || res.returncode === -4071) {
      throw new CloneError(
        `git could not be spawned (EINVAL). On Windows, ensure git.exe is on ` +
        `PATH ahead of any .cmd shim (e.g. install Git for Windows).`,
      );
    }
    const tail = (stderr || res.stdout || "").trim().slice(-800);
    throw new CloneError(`git clone failed (exit ${res.returncode}): ${tail}`);
  }
  return resolved;
}
