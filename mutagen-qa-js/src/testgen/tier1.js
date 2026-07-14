/**
 * Tier 1: happy-path + example-based tests using node's built-in test runner.
 *
 * Emits a single ESM module that imports the target and calls `test(...)`
 * from 'node:test' with assertions from 'node:assert/strict'. Same one-shot
 * generation strategy as the Python side; multi-round refinement is handled
 * by the loop, not here.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { focusDirective, readFocus, stripFences } from "./util.js";

const SYSTEM =
  "You are a senior JavaScript QA engineer. Write tight, high-signal tests " +
  "using Node's built-in test runner ('node:test') and assertions from " +
  "'node:assert/strict'. Cover happy paths, boundary cases, and error paths. " +
  "NO commentary, NO markdown fences: return ONLY valid ES module source for " +
  "a single test file. Import the target as: `import { <symbols> } from './target.js';`. " +
  "Do not import anything outside 'node:test' / 'node:assert' / the target.";

const USER_TEMPLATE = (source) => `Target module source (file: target.js):

\`\`\`javascript
${source}
\`\`\`

Write a test file \`test_generated.js\` that:
- imports the public symbols from \`./target.js\`,
- uses \`node:test\`'s \`test(...)\` (not \`describe/it\` blocks) so each case
  is one top-level call,
- covers the happy path with a few well-chosen inputs,
- has separate tests for each documented error path using
  \`assert.throws(() => fn(bad), <ErrorType>)\`,
- when checking errors, assert ONLY the error constructor (e.g. TypeError).
  Do NOT assert exact message text -- wording is brittle,
- keeps the total test count lean (< 20 tests) but bug-catching,
- has NO comments explaining what code does; only short docstring-style
  strings on tests when the intent isn't obvious.

Return ONLY the JavaScript source, nothing else.
`;

export async function generateT1(llm, { targetSource, sourceText }) {
  // Accept either a preloaded string (loop.js caches it once) or fall back
  // to reading from disk for one-off callers (tests, MCP paths).
  const source = sourceText ?? readFileSync(targetSource, "utf8");
  const focus = readFocus(path.dirname(targetSource));
  const user = focusDirective(focus) + USER_TEMPLATE(source);
  const resp = await llm.complete("codegen", { system: SYSTEM, user });
  return { source: stripFences(resp.text), finishReason: resp.finishReason };
}

export { hasTests } from "./util.js";
