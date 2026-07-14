/**
 * Invoke Node's built-in test runner against generated test files.
 *
 * Why node --test instead of jest: it ships with Node 20+, no npm install
 * needed, ESM works out of the box, and the exit-code contract Stryker needs
 * (0 = all passed, non-zero = at least one failure) is what we already emit.
 *
 * Import shape used by generated tests:
 *
 *     import { slugify } from './target.js';
 *     import { test } from 'node:test';
 *     import assert from 'node:assert/strict';
 *
 * The generator prompt (tier1.js) enforces this convention.
 */

import { runSubprocess } from "./executor.js";

// Per-test cap: 5s matches Stryker's per-mutant timeout. Without it, a
// generated `while(true)` inside a test hangs until the outer timeoutS
// (default 30s), and node --test can't report which test wedged.
const DEFAULT_PER_TEST_TIMEOUT_MS = 5000;

export async function runNodeTest(workdir, testFiles, { timeoutS = 30, perTestTimeoutMs = DEFAULT_PER_TEST_TIMEOUT_MS } = {}) {
  const argv = [
    process.execPath,
    "--test",
    `--test-timeout=${perTestTimeoutMs}`,
    ...testFiles,
  ];
  return runSubprocess(argv, { cwd: workdir, timeoutS });
}
