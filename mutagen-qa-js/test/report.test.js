import { test } from "node:test";
import assert from "node:assert/strict";

import { parseStrykerReport } from "../src/mutation/runner.js";

const FAKE_REPORT = {
  schemaVersion: "2",
  files: {
    "target.js": {
      language: "javascript",
      source: 'export function f(x) {\n  if (x > 0) { return 1; }\n  return 0;\n}\n',
      mutants: [
        {
          id: "1",
          mutatorName: "EqualityOperator",
          replacement: ">=",
          status: "Killed",
          location: { start: { line: 2, column: 8 }, end: { line: 2, column: 9 } },
        },
        {
          id: "2",
          mutatorName: "BooleanLiteral",
          replacement: "false",
          status: "Survived",
          location: { start: { line: 2, column: 15 }, end: { line: 2, column: 16 } },
        },
        {
          id: "3",
          mutatorName: "ArithmeticOperator",
          replacement: "-",
          status: "Survived",
          location: { start: { line: 2, column: 20 }, end: { line: 2, column: 21 } },
        },
        {
          id: "4",
          mutatorName: "BlockStatement",
          replacement: "{}",
          status: "Timeout",
          location: { start: { line: 3, column: 2 }, end: { line: 3, column: 12 } },
        },
      ],
    },
  },
};

test("parseStrykerReport totals + kill_rate ignore timeouts/skipped", () => {
  const report = parseStrykerReport(FAKE_REPORT);
  assert.equal(report.total, 4);
  assert.equal(report.killed, 1);
  assert.equal(report.survived, 2);
  assert.equal(report.timeout, 1);
  // killable = total - timeout - skipped = 3, killed = 1 => 1/3.
  assert.ok(Math.abs(report.kill_rate - 1 / 3) < 1e-9);
});

test("parseStrykerReport classifies mutator names into cross-language kinds", () => {
  const report = parseStrykerReport(FAKE_REPORT);
  const kinds = Object.fromEntries(report.survivors.map((s) => [s.id, s.kind]));
  assert.equal(kinds["2"], "constant"); // BooleanLiteral
  assert.equal(kinds["3"], "arithmetic"); // ArithmeticOperator
});

test("parseStrykerReport emits unified-diff-like snippets for survivors", () => {
  const report = parseStrykerReport(FAKE_REPORT);
  const s = report.survivors.find((x) => x.id === "3");
  assert.ok(s.diff.includes("--- target.js"));
  assert.ok(s.diff.includes("+++ target.js"));
  assert.ok(s.diff.includes("+-")); // replacement '-'
});
