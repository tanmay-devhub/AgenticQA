import { test } from "node:test";
import assert from "node:assert/strict";

import { planSpecs, _internal } from "../src/agent/planner.js";

function classified(verdict, mutant) {
  return { verdict, reason: "", mutant };
}
function m(id, { line = 10, kind = "constant", file = "target.js", diff = "diff" } = {}) {
  return { id, file, line, status: "survived", kind, diff };
}

test("planner: empty in -> empty out", () => {
  assert.deepEqual(planSpecs([]), []);
});

test("planner: drops non-real_gap survivors", () => {
  const specs = planSpecs([
    classified("equivalent", m("1")),
    classified("message_noise", m("2")),
  ]);
  assert.deepEqual(specs, []);
});

test("planner: clusters real_gap survivors by line window", () => {
  const specs = planSpecs([
    classified("real_gap", m("1", { line: 5, kind: "comparison" })),
    classified("real_gap", m("2", { line: 6, kind: "comparison" })),
    classified("real_gap", m("3", { line: 30, kind: "arithmetic" })),
  ]);
  assert.equal(specs.length, 2, "two clusters: near line 5-6 and near line 30");
  const dominantKinds = specs.map((s) => s.dominant_kind).sort();
  assert.deepEqual(dominantKinds, ["arithmetic", "comparison"]);
});

test("planner: dominant kind wins per cluster", () => {
  const specs = planSpecs([
    classified("real_gap", m("1", { line: 10, kind: "constant" })),
    classified("real_gap", m("2", { line: 11, kind: "constant" })),
    classified("real_gap", m("3", { line: 12, kind: "comparison" })),
  ]);
  assert.equal(specs.length, 1);
  assert.equal(specs[0].dominant_kind, "constant");
  assert.match(specs[0].technique_hint, /pin the constant/);
});

test("planner: unknown kind maps to 'other' technique", () => {
  const specs = planSpecs([classified("real_gap", m("1", { kind: "weird_kind" }))]);
  assert.equal(specs.length, 1);
  assert.equal(specs[0].dominant_kind, "weird_kind"); // dominant preserves label
  assert.match(specs[0].technique_hint, /example-based/); // but hint falls back to 'other'
});

test("planner: missing lines within cluster span become uncovered_lines hint", () => {
  const specs = planSpecs(
    [classified("real_gap", m("1", { line: 10 })), classified("real_gap", m("2", { line: 12 }))],
    { missingLines: [8, 11, 25, 50] },
  );
  assert.equal(specs.length, 1);
  // Cluster span: min-2..max+8 = 8..20. Lines 8 and 11 fall inside.
  assert.deepEqual(specs[0].uncovered_lines, [8, 11]);
});

test("planner: linesInClusterSpan empty when no missing input", () => {
  assert.deepEqual(_internal.linesInClusterSpan([{ line: 10 }], []), []);
});
