import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, existsSync, readFileSync } from "node:fs";
import path from "node:path";
import os from "node:os";

import { classifySurvivors, _internal } from "../src/agent/classifier.js";
import { FakeLLM } from "./_fake_llm.js";

function tmpdir() {
  return mkdtempSync(path.join(os.tmpdir(), "mutagen-js-classifier-"));
}

function fakeSurvivor(id, diff, extras = {}) {
  return { id, file: "target.js", line: 5, status: "survived", kind: "constant", diff, ...extras };
}

test("classifier: empty survivors returns []", async () => {
  const llm = new FakeLLM();
  const dir = tmpdir();
  writeFileSync(path.join(dir, "target.js"), "export const x = 1;\n", "utf8");
  const out = await classifySurvivors({
    llm,
    targetSource: path.join(dir, "target.js"),
    survivors: [],
    cacheDir: path.join(dir, ".mutagen"),
  });
  assert.deepEqual(out, []);
  assert.equal(llm.calls.length, 0);
});

test("classifier: no diff defaults to real_gap without calling LLM", async () => {
  const llm = new FakeLLM();
  const dir = tmpdir();
  writeFileSync(path.join(dir, "target.js"), "export const x = 1;\n", "utf8");
  const out = await classifySurvivors({
    llm,
    targetSource: path.join(dir, "target.js"),
    survivors: [fakeSurvivor("1", null)],
    cacheDir: path.join(dir, ".mutagen"),
  });
  assert.equal(out[0].verdict, "real_gap");
  assert.match(out[0].reason, /no diff/);
  assert.equal(llm.calls.length, 0);
});

test("classifier: parses JSON verdict from LLM and caches by diff hash", async () => {
  const llm = new FakeLLM();
  llm.queueResponse('{"verdict":"equivalent","reason":"guarded by strip earlier"}');
  const dir = tmpdir();
  writeFileSync(path.join(dir, "target.js"), "export const x = 1;\n", "utf8");
  const cacheDir = path.join(dir, ".mutagen");

  const out1 = await classifySurvivors({
    llm,
    targetSource: path.join(dir, "target.js"),
    survivors: [fakeSurvivor("1", "@@\n-x = 1\n+x = 2")],
    cacheDir,
  });
  assert.equal(out1[0].verdict, "equivalent");
  assert.equal(llm.calls.length, 1);
  // Cache file written.
  assert.ok(existsSync(path.join(cacheDir, "classifier_cache.json")));

  // Re-run with same diff -- must hit cache, NOT call LLM.
  const out2 = await classifySurvivors({
    llm,
    targetSource: path.join(dir, "target.js"),
    survivors: [fakeSurvivor("1", "@@\n-x = 1\n+x = 2")],
    cacheDir,
  });
  assert.equal(out2[0].verdict, "equivalent");
  assert.equal(llm.calls.length, 1, "second call must hit cache");
});

test("classifier: LLM failure defaults to real_gap and does NOT cache", async () => {
  const llm = new FakeLLM();
  llm._queue.push({ throw: Object.assign(new Error("boom"), { name: "APIConnectionError" }) });
  const dir = tmpdir();
  writeFileSync(path.join(dir, "target.js"), "x", "utf8");
  const cacheDir = path.join(dir, ".mutagen");

  const out = await classifySurvivors({
    llm,
    targetSource: path.join(dir, "target.js"),
    survivors: [fakeSurvivor("1", "@@\n-a\n+b")],
    cacheDir,
  });
  assert.equal(out[0].verdict, "real_gap");
  assert.match(out[0].reason, /planner call failed/);
  // Failure must NOT be cached so a later retry can still succeed.
  const cachePath = path.join(cacheDir, "classifier_cache.json");
  if (existsSync(cachePath)) {
    const c = JSON.parse(readFileSync(cachePath, "utf8"));
    assert.equal(Object.keys(c).length, 0);
  }
});

test("classifier: unparseable LLM output falls back to real_gap", () => {
  assert.equal(_internal.parseVerdict("no json here").verdict, "real_gap");
  assert.equal(_internal.parseVerdict("{not-json}").verdict, "real_gap");
  assert.equal(_internal.parseVerdict('{"verdict":"nonsense","reason":"?"}').verdict, "real_gap");
});
