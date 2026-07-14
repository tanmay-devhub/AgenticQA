import { test } from "node:test";
import assert from "node:assert/strict";

import { hasTests, stripFences, focusDirective } from "../src/testgen/util.js";

test("stripFences strips a plain ```javascript fence", () => {
  const src = "```javascript\nconst x = 1;\n```";
  assert.equal(stripFences(src), "const x = 1;\n");
});

test("stripFences returns bare source unchanged (adds trailing newline)", () => {
  assert.equal(stripFences("const x = 1;"), "const x = 1;\n");
});

test("hasTests detects a top-level test() call", () => {
  const src = "import { test } from 'node:test';\ntest('a', () => {});";
  assert.equal(hasTests(src), true);
});

test("hasTests detects an it() call inside a describe block", () => {
  const src = "describe('x', () => { it('y', () => {}); });";
  assert.equal(hasTests(src), true);
});

test("hasTests returns false when source is only imports / thinking output", () => {
  assert.equal(hasTests(""), false);
  assert.equal(hasTests("import { test } from 'node:test';\n// TODO\n"), false);
  assert.equal(
    hasTests("<think>\nplanning strategy for tests\n</think>\n"),
    false,
  );
});

test("focusDirective returns empty string when no focus", () => {
  assert.equal(focusDirective(null), "");
  assert.equal(focusDirective(""), "");
});

test("focusDirective embeds the focus text", () => {
  const out = focusDirective("cover unicode fold at NFKD boundary");
  assert.match(out, /TESTING FOCUS/);
  assert.match(out, /unicode fold/);
});
