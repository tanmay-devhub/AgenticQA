import { test } from "node:test";
import assert from "node:assert/strict";

import { LoopResult } from "../src/agent/loop_result.js";

function round(index, { report = null, usage = null } = {}) {
  return {
    index,
    tier: 1,
    tests_path: `test_round_${index}.js`,
    pytest_ok: true,
    report,
    usage: usage ?? {
      codegen: { calls: 1, prompt_tokens: 100, completion_tokens: 50 },
      planner: { calls: 0, prompt_tokens: 0, completion_tokens: 0 },
      analysis: { calls: 0, prompt_tokens: 0, completion_tokens: 0 },
    },
  };
}

test("LoopResult.finalReport returns the last round with a report", () => {
  const r = new LoopResult("/tmp/wd");
  r.rounds.push(round(1, { report: { kill_rate: 0.5 } }));
  r.rounds.push(round(2, { report: { kill_rate: 0.8 } }));
  r.rounds.push(round(3, { report: null }));
  assert.equal(r.finalReport.kill_rate, 0.8);
});

test("LoopResult.finalReport returns null when no round has a report", () => {
  const r = new LoopResult("/tmp/wd");
  r.rounds.push(round(1, { report: null }));
  assert.equal(r.finalReport, null);
  assert.equal(r.finalKillRate, null);
});

test("LoopResult.totalUsage sums across roles and rounds", () => {
  const r = new LoopResult("/tmp/wd");
  r.rounds.push(round(1)); // 100 + 50 codegen
  r.rounds.push(round(2)); // 100 + 50 codegen
  const t = r.totalUsage;
  assert.equal(t.codegen.calls, 2);
  assert.equal(t.codegen.prompt_tokens, 200);
  assert.equal(t.codegen.completion_tokens, 100);
  assert.equal(t.analysis.calls, 0);
});

test("LoopResult.toJSON matches the run.json shape", () => {
  const r = new LoopResult("/tmp/wd");
  r.stopped_reason = "no survivors";
  r.rounds.push(round(1, { report: { kill_rate: 0.9 } }));
  const dump = JSON.parse(JSON.stringify(r));
  assert.equal(dump.workdir, "/tmp/wd");
  assert.equal(dump.stopped_reason, "no survivors");
  assert.equal(dump.rounds.length, 1);
  assert.equal(dump.final_kill_rate, 0.9);
  assert.ok(dump.total_usage);
});
