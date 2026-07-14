import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import path from "node:path";
import os from "node:os";

import { MAX_REPAIR_ATTEMPTS, repair } from "../src/testgen/repair.js";
import { FakeLLM } from "./_fake_llm.js";

function tmpdir() {
  return mkdtempSync(path.join(os.tmpdir(), "mutagen-js-repair-"));
}

test("repair: passes broken tests + stderr to codegen and returns stripped source", async () => {
  const dir = tmpdir();
  const target = path.join(dir, "target.js");
  const tests = path.join(dir, "test.js");
  writeFileSync(target, "export const x = 1;\n", "utf8");
  writeFileSync(tests, "totally broken\n", "utf8");

  const llm = new FakeLLM();
  llm.queueResponse("```javascript\nconst x = 1;\n```");
  const out = await repair(llm, {
    targetSource: target,
    testsPath: tests,
    stderr: "SyntaxError: Unexpected identifier",
    attempt: 0,
  });
  assert.equal(out, "const x = 1;\n");
  assert.equal(llm.calls.length, 1);
  assert.match(llm.calls[0].user, /Current broken test module/);
  assert.match(llm.calls[0].user, /SyntaxError/);
});

test("repair: attempt >= 1 appends the 'try a different tactic' hint", async () => {
  const dir = tmpdir();
  writeFileSync(path.join(dir, "target.js"), "x", "utf8");
  writeFileSync(path.join(dir, "test.js"), "x", "utf8");

  const llm = new FakeLLM();
  llm.queueResponse("fixed()");
  await repair(llm, {
    targetSource: path.join(dir, "target.js"),
    testsPath: path.join(dir, "test.js"),
    stderr: "",
    attempt: 1,
  });
  assert.match(llm.calls[0].user, /earlier repair attempt still failed/);
});

test("repair: bumps and restores codegen temperature on attempt 1", async () => {
  const dir = tmpdir();
  writeFileSync(path.join(dir, "target.js"), "x", "utf8");
  writeFileSync(path.join(dir, "test.js"), "x", "utf8");

  const llm = new FakeLLM();
  llm._config = { llm: { codegen: { temperature: 0.2 } } };
  llm.queueResponse("fixed()");

  // Capture the temperature seen during the call by inspecting _config.
  const observed = [];
  const origComplete = llm.complete.bind(llm);
  llm.complete = async (role, args) => {
    observed.push(llm._config.llm.codegen.temperature);
    return origComplete(role, args);
  };

  await repair(llm, {
    targetSource: path.join(dir, "target.js"),
    testsPath: path.join(dir, "test.js"),
    stderr: "",
    attempt: 1,
  });
  assert.ok(observed[0] > 0.2, "temperature bumped during attempt >= 1");
  assert.equal(llm._config.llm.codegen.temperature, 0.2, "restored after call");
});

test("MAX_REPAIR_ATTEMPTS is 2 (matches Python side)", () => {
  assert.equal(MAX_REPAIR_ATTEMPTS, 2);
});
