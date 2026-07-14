/**
 * Shared helpers used by tier1/tier2/tier3 generators.
 *
 * Mirrors mutagen-qa/src/mutagen/testgen/tier1.py's _strip_fences / has_tests
 * / focus.txt reading.
 */

import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const FENCE_RE = /^```(?:javascript|js|typescript|ts)?\s*\n([\s\S]*?)\n```\s*$/;

// Detects Node built-in `test(...)` calls or jest `it/test(...)` calls at any
// nesting. If codegen returns only an import + describe scaffold with no test
// bodies, this returns false and the loop bails on the round.
//
// Deliberately permissive: matches `test.each`, `test.only`, `it.skip`, etc.
// These are legitimate test declarations -- tightening the regex would
// reject valid parametrized suites the codegen may produce.
const TEST_CALL_RE = /(?:^|[^A-Za-z0-9_$])(?:test|it)(?:\.\w+)?\s*\(/m;

const FOCUS_FILENAME = "focus.txt";

export function stripFences(text) {
  const stripped = text.trim();
  const m = FENCE_RE.exec(stripped);
  if (m) return m[1].trim() + "\n";
  return stripped + (stripped.endsWith("\n") ? "" : "\n");
}

export function hasTests(source) {
  return TEST_CALL_RE.test(source);
}

export function readFocus(workdir) {
  const p = path.join(workdir, FOCUS_FILENAME);
  if (!existsSync(p)) return null;
  const text = readFileSync(p, "utf8").trim();
  return text || null;
}

export function focusDirective(focus) {
  if (!focus) return "";
  return (
    "TESTING FOCUS (user priority):\n" +
    focus +
    "\n\n" +
    "Weight your test cases toward this concern first. If the focus is " +
    "narrow (a specific function, edge case, or code path), you may still " +
    "add a couple of sanity tests for the rest, but the MAJORITY of the " +
    "module should target the focus above.\n\n"
  );
}
