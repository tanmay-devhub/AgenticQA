import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";

import { detectLanguages } from "../src/repo/detect.js";

function tmpdir() {
  return mkdtempSync(path.join(os.tmpdir(), "mutagen-js-detect-"));
}

function write(root, rel, content = "x") {
  const full = path.join(root, rel);
  mkdirSync(path.dirname(full), { recursive: true });
  writeFileSync(full, content, "utf8");
}

test("detect: empty dir -> {}", () => {
  assert.deepEqual(detectLanguages(tmpdir()), {});
});

test("detect: counts .js/.mjs/.cjs as javascript", () => {
  const dir = tmpdir();
  write(dir, "a.js");
  write(dir, "b.mjs");
  write(dir, "c.cjs");
  const langs = detectLanguages(dir);
  assert.equal(langs.javascript, 3);
});

test("detect: skips node_modules and .git", () => {
  const dir = tmpdir();
  write(dir, "src/a.js");
  write(dir, "node_modules/react/index.js");
  write(dir, "node_modules/react/deep/b.js");
  write(dir, ".git/objects/x");
  const langs = detectLanguages(dir);
  assert.equal(langs.javascript, 1, "only src/a.js should be counted");
});

test("detect: skips hidden dot-dirs", () => {
  const dir = tmpdir();
  write(dir, "src/a.js");
  write(dir, ".cache/b.js");
  const langs = detectLanguages(dir);
  assert.equal(langs.javascript, 1);
});

test("detect: returns descending-count order (dominant first)", () => {
  const dir = tmpdir();
  write(dir, "a.py");
  write(dir, "b.py");
  write(dir, "c.py");
  write(dir, "d.js");
  write(dir, "e.ts");
  const langs = detectLanguages(dir);
  const keys = Object.keys(langs);
  assert.equal(keys[0], "python", "python has 3 files, must come first");
});

test("detect: non-existent path -> {}", () => {
  assert.deepEqual(detectLanguages(path.join(tmpdir(), "does-not-exist")), {});
});
