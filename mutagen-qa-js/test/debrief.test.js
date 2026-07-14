import { test } from "node:test";
import assert from "node:assert/strict";

import { _internal } from "../src/agent/debrief.js";
const { parseFailingTests } = _internal;

test("debrief extracts test name from `not ok N - <name>`", () => {
  const stdout = "ok 1 - passing\nnot ok 2 - failing test\n1..2\n";
  const out = parseFailingTests(stdout);
  assert.equal(out.length, 1);
  assert.equal(out[0].name, "failing test");
});

test("debrief captures error message from YAML block", () => {
  const stdout = `
not ok 1 - equality
  ---
  duration_ms: 3.2
  failureType: 'testCodeFailure'
  error: 'Expected 1 !== 2'
  code: 'ERR_ASSERTION'
  ...
1..1
`;
  const out = parseFailingTests(stdout);
  assert.equal(out.length, 1);
  assert.equal(out[0].name, "equality");
  assert.equal(out[0].reason, "Expected 1 !== 2");
});

test("debrief handles multiple failures with YAML blocks each", () => {
  const stdout = `
not ok 1 - first
  ---
  error: 'boom one'
  ...
not ok 2 - second
  ---
  error: 'boom two'
  ...
1..2
`;
  const out = parseFailingTests(stdout);
  assert.equal(out.length, 2);
  assert.equal(out[0].reason, "boom one");
  assert.equal(out[1].reason, "boom two");
});

test("debrief tolerates a YAML block without an error: field", () => {
  const stdout = "not ok 1 - naked\n  ---\n  duration_ms: 1\n  ...\n1..1\n";
  const out = parseFailingTests(stdout);
  assert.equal(out[0].name, "naked");
  assert.equal(out[0].reason, "");
});

test("debrief unescapes \\n and quotes inside the YAML error string", () => {
  const stdout = "not ok 1 - multiline\n  ---\n  error: 'a\\nb'\n  ...\n1..1\n";
  const out = parseFailingTests(stdout);
  assert.equal(out[0].reason, "a b");
});
