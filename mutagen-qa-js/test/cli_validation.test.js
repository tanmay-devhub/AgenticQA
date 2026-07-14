import { test } from "node:test";
import assert from "node:assert/strict";

import { hasTests } from "../src/testgen/util.js";

test("hasTests matches test.each() (parametrized jest)", () => {
  assert.equal(hasTests("test.each([[1,2]])('sum', (a,b) => {});"), true);
});

test("hasTests matches test.only() and it.skip()", () => {
  assert.equal(hasTests("test.only('a', () => {});"), true);
  assert.equal(hasTests("it.skip('b', () => {});"), true);
});

test("hasTests still ignores identifiers ending in test", () => {
  assert.equal(hasTests("const attest = 1;"), false);
});
