/**
 * Loop orchestration tests. Real node --test / Stryker are replaced with
 * injected fakes so we can assert on the exact call sequence + persisted
 * artifacts without spawning real processes.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import path from "node:path";
import os from "node:os";

import { runLoop } from "../src/agent/loop.js";
import { getConfig } from "../src/config.js";
import { FakeLLM } from "./_fake_llm.js";

function tmpdir() {
  return mkdtempSync(path.join(os.tmpdir(), "mutagen-js-loop-"));
}

function makeReport({ killed = 10, total = 12, survivors = [] } = {}) {
  const killable = total - survivors.filter((s) => s.status === "timeout").length;
  return {
    total,
    killed,
    survived: survivors.length,
    timeout: 0,
    suspicious: 0,
    skipped: 0,
    kill_rate: killable > 0 ? killed / killable : 0,
    survivors,
    disabled_types: [],
  };
}

function survivor(id, line, kind = "constant") {
  return {
    id,
    file: "target.js",
    line,
    status: "survived",
    kind,
    diff: `--- target.js\n+++ target.js\n@@ line ${line} @@\n-x\n+y\n`,
  };
}

function writeTarget(dir, source = "export function f(x){return x+1;}\n") {
  const targetDir = path.dirname(dir);
  const targetPath = path.join(targetDir, "target.js");
  writeFileSync(targetPath, source, "utf8");
  return targetPath;
}

test("loop: crash inside a round is recorded in run.json's stopped_reason", async () => {
  const workdir = tmpdir();
  const target = writeTarget(workdir);

  const llm = new FakeLLM();
  llm.queueResponse("import { test } from 'node:test';\ntest('a', () => {});");
  const cfg = getConfig();
  const runNodeTest = async () => { throw new Error("simulated node crash"); };
  const runStryker = async () => ({ report: makeReport(), runResult: {} });

  await assert.rejects(
    () => runLoop({ target, workdir, cfg, llm, maxRounds: 1, runNodeTest, runStryker }),
    /simulated node crash/,
  );

  // run.json exists and stopped_reason names the crash.
  const runJson = JSON.parse(readFileSync(path.join(workdir, "run.json"), "utf8"));
  assert.match(runJson.stopped_reason, /^crashed: /);
  assert.match(runJson.stopped_reason, /simulated node crash/);
});

test("loop: T1 passes, no survivors -> stops after round 1", async () => {
  const workdir = tmpdir();
  const target = writeTarget(workdir);

  const llm = new FakeLLM();
  llm.queueResponse("import { test } from 'node:test';\ntest('a', () => {});");

  const cfg = getConfig();
  const runNodeTest = async () => ({ returncode: 0, stdout: "ok 1 - a\n", stderr: "", timedOut: false });
  const runStryker = async () => ({ report: makeReport({ killed: 12, total: 12 }), runResult: {} });

  const result = await runLoop({
    target, workdir, cfg, llm, maxRounds: 3, runNodeTest, runStryker,
  });

  assert.equal(result.stopped_reason, "no survivors after round 1");
  assert.equal(result.rounds.length, 1);
  assert.equal(result.rounds[0].tier, 1);
  assert.ok(existsSync(path.join(workdir, "test_round_1.js")));
  assert.ok(existsSync(path.join(workdir, "run.json")));
  assert.ok(existsSync(path.join(workdir, "round_1_report.json")));
  assert.ok(existsSync(path.join(workdir, "round_1_debrief.md")));
});

test("loop: T1 with survivors -> T2 called with planner specs", async () => {
  const workdir = tmpdir();
  const target = writeTarget(workdir);

  const llm = new FakeLLM();
  llm.queueResponse("import { test } from 'node:test';\ntest('a', () => {});"); // T1
  llm.queueResponse('{"verdict":"real_gap","reason":"boundary"}'); // classifier for survivor 1
  llm.queueResponse('{"verdict":"real_gap","reason":"boundary"}'); // classifier for survivor 2
  llm.queueResponse("import { test } from 'node:test';\ntest('b', () => {});"); // T2

  const cfg = getConfig();
  let callN = 0;
  const runNodeTest = async () => ({ returncode: 0, stdout: "", stderr: "", timedOut: false });
  const runStryker = async () => {
    callN += 1;
    if (callN === 1) {
      return { report: makeReport({ killed: 8, total: 12, survivors: [survivor("1", 5), survivor("2", 6)] }), runResult: {} };
    }
    return { report: makeReport({ killed: 12, total: 12 }), runResult: {} };
  };

  const result = await runLoop({
    target, workdir, cfg, llm, maxRounds: 3, runNodeTest, runStryker,
  });

  assert.equal(result.rounds.length, 2);
  assert.equal(result.rounds[1].tier, 2, "round 2 must be T2");
  assert.equal(result.stopped_reason, "no survivors remaining");
  // Handoff appended to round_1_debrief.md.
  const debrief = readFileSync(path.join(workdir, "round_1_debrief.md"), "utf8");
  assert.match(debrief, /handoff to round 2/);
  assert.match(debrief, /real_gap.*2/);
});

test("loop: plateau after T2 escalates to T3 exactly once", async () => {
  const workdir = tmpdir();
  const target = writeTarget(workdir);

  const llm = new FakeLLM();
  llm.queueResponse("import { test } from 'node:test';\ntest('t1', () => {});"); // T1
  llm.queueResponse('{"verdict":"real_gap","reason":"r"}'); // classifier for R1 survivor
  llm.queueResponse("import { test } from 'node:test';\ntest('t2', () => {});"); // T2
  llm.queueResponse('{"verdict":"real_gap","reason":"r"}'); // classifier for R2 survivor
  llm.queueResponse("import { test } from 'node:test';\ntest('t3', () => {});"); // T3

  const cfg = getConfig();
  // Report the SAME kill rate for round 2 as round 1 to trigger plateau.
  let n = 0;
  const runNodeTest = async () => ({ returncode: 0, stdout: "", stderr: "", timedOut: false });
  const runStryker = async () => {
    n += 1;
    if (n === 1 || n === 2) {
      return { report: makeReport({ killed: 10, total: 12, survivors: [survivor("9", 10)] }), runResult: {} };
    }
    return { report: makeReport({ killed: 12, total: 12 }), runResult: {} };
  };

  const result = await runLoop({
    target, workdir, cfg, llm, maxRounds: 4, runNodeTest, runStryker,
  });

  const tiers = result.rounds.map((r) => r.tier);
  assert.deepEqual(tiers, [1, 2, 3], "T1 -> T2 (plateau) -> T3");
});

test("loop: empty codegen output halts the round without running Stryker", async () => {
  const workdir = tmpdir();
  const target = writeTarget(workdir);

  const llm = new FakeLLM();
  llm.queueResponse("<think>\nI would write tests but ran out of budget.\n</think>\n");

  const cfg = getConfig();
  let nodeTestCalled = false;
  let strykerCalled = false;
  const runNodeTest = async () => { nodeTestCalled = true; return { returncode: 0, stdout: "", stderr: "", timedOut: false }; };
  const runStryker = async () => { strykerCalled = true; return { report: makeReport(), runResult: {} }; };

  const result = await runLoop({
    target, workdir, cfg, llm, maxRounds: 3, runNodeTest, runStryker,
  });

  assert.match(result.stopped_reason, /codegen produced no tests in round 1/);
  assert.equal(nodeTestCalled, false, "node --test must NOT run");
  assert.equal(strykerCalled, false, "Stryker must NOT run");
  assert.equal(result.rounds[0].no_codegen_output, true);
});

test("loop: codegen truncated (finish_reason=length) halts round even when tests appear present", async () => {
  const workdir = tmpdir();
  const target = writeTarget(workdir);

  const llm = new FakeLLM();
  llm.queueResponse(
    "import { test } from 'node:test';\ntest('half', () => { assert.equal(1,",
    { finishReason: "length" },
  );

  const cfg = getConfig();
  let nodeTestCalled = false;
  let strykerCalled = false;
  const runNodeTest = async () => { nodeTestCalled = true; return { returncode: 0, stdout: "", stderr: "", timedOut: false }; };
  const runStryker = async () => { strykerCalled = true; return { report: makeReport(), runResult: {} }; };

  const result = await runLoop({
    target, workdir, cfg, llm, maxRounds: 3, runNodeTest, runStryker,
  });

  assert.match(result.stopped_reason, /codegen truncated in round 1/);
  assert.match(result.stopped_reason, /finish_reason=length/);
  assert.equal(nodeTestCalled, false);
  assert.equal(strykerCalled, false);
  assert.equal(result.rounds[0].no_codegen_output, true);
});

test("loop: broken T1 triggers repair, passes on retry, then Stryker runs", async () => {
  const workdir = tmpdir();
  const target = writeTarget(workdir);

  const llm = new FakeLLM();
  llm.queueResponse("import { test } from 'node:test';\ntest('a', () => { totallyBroken(); });"); // T1
  llm.queueResponse("import { test } from 'node:test';\ntest('a', () => {});"); // repair attempt 0

  const cfg = getConfig();
  let nCalls = 0;
  const runNodeTest = async () => {
    nCalls += 1;
    if (nCalls === 1) return { returncode: 1, stdout: "not ok 1 - a # ReferenceError\n", stderr: "err", timedOut: false };
    return { returncode: 0, stdout: "ok 1 - a\n", stderr: "", timedOut: false };
  };
  const runStryker = async () => ({ report: makeReport({ killed: 12, total: 12 }), runResult: {} });

  const result = await runLoop({
    target, workdir, cfg, llm, maxRounds: 1, runNodeTest, runStryker,
  });

  assert.equal(result.rounds[0].pytest_ok, true);
  assert.equal(result.rounds[0].repaired, true);
  assert.ok(result.rounds[0].initial_pytest_result);
});
