/**
 * Multi-round loop driver for the JS pipeline.
 *
 * Round 1 = T1. Rounds 2..N = T2 driven by classified real_gap survivors from
 * the previous round, escalating to T3 (fast-check property-based) once on
 * plateau. Stops when kill rate plateaus, wall clock hits, no real survivors
 * remain, or max_rounds is reached.
 *
 * JSON output shape (target.js, test_round_N.js, round_N_report.json,
 * run.json) matches the Python side's exactly so the FastAPI dashboard
 * renders JS runs unchanged.
 */

import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { classifySurvivors } from "./classifier.js";
import { appendHandoff, writeRoundBody } from "./debrief.js";
import { LoopResult } from "./loop_result.js";
import { planSpecs } from "./planner.js";
import { runNodeTest as defaultRunNodeTest } from "../sandbox/node_test.js";
import { runStryker as defaultRunStryker } from "../mutation/runner.js";
import { generateT1, hasTests } from "../testgen/tier1.js";
import { generateT2 } from "../testgen/tier2.js";
import { generateT3 } from "../testgen/tier3.js";
import { MAX_REPAIR_ATTEMPTS, repair as repairTests } from "../testgen/repair.js";

const NO_CODEGEN_STDERR =
  "codegen returned no test functions; node --test and Stryker were skipped";

// Provider strings that indicate the model hit its output budget mid-completion.
// Gemini uses "MAX_TOKENS"; OpenAI-compatible endpoints use "length".
const TRUNCATED_FINISH_REASONS = new Set(["MAX_TOKENS", "length"]);

function isTruncated(finishReason) {
  return finishReason != null && TRUNCATED_FINISH_REASONS.has(finishReason);
}

function prepareWorkdir(target, workdir) {
  mkdirSync(workdir, { recursive: true });
  const dest = path.join(workdir, "target.js");
  if (!existsSync(dest) || path.resolve(target) !== dest) {
    copyFileSync(target, dest);
  }
  return dest;
}

function emptyCodegenRound({ index, tier, testsPath }) {
  return {
    index,
    tier,
    tests_path: testsPath,
    pytest_ok: false,
    pytest_result: {
      returncode: -1,
      stdout: "",
      stderr: NO_CODEGEN_STDERR,
      timed_out: false,
    },
    report: null,
    elapsed_s: 0,
    repaired: false,
    initial_pytest_result: null,
    no_codegen_output: true,
    usage: {
      codegen: { calls: 0, prompt_tokens: 0, completion_tokens: 0 },
      planner: { calls: 0, prompt_tokens: 0, completion_tokens: 0 },
      analysis: { calls: 0, prompt_tokens: 0, completion_tokens: 0 },
    },
  };
}

function serializeUsageDelta(delta) {
  const conv = (b) => ({
    calls: b.calls,
    prompt_tokens: b.promptTokens,
    completion_tokens: b.completionTokens,
  });
  return {
    codegen: conv(delta.codegen),
    planner: conv(delta.planner),
    analysis: conv(delta.analysis),
  };
}

function persistRound(workdir, round) {
  writeFileSync(
    path.join(workdir, `round_${round.index}_report.json`),
    JSON.stringify(round, null, 2),
    "utf8",
  );
}

function persistFinal(workdir, result) {
  writeFileSync(path.join(workdir, "run.json"), JSON.stringify(result, null, 2), "utf8");
}

// `ctx`: environment (workdir, config, llm, injected runners) that doesn't
// change round-to-round. `plan`: everything about the specific round being
// executed. Split so callers don't spread 10 named parameters at the call
// site, and so adding a new round-scoped field doesn't touch every caller.
async function runRound(ctx, plan) {
  const { workdir, cfg, llm, runNodeTest, runStryker } = ctx;
  const { testsPath, allTestFiles, targetRel, index, tier } = plan;
  const t0 = Date.now();
  const testFiles = allTestFiles ?? [path.basename(testsPath)];
  const targetSrcPath = path.join(workdir, "target.js");
  let nodeTest = await runNodeTest(workdir, testFiles, {
    timeoutS: cfg.sandbox.nodeTimeoutS,
  });
  let testsOk = nodeTest.returncode === 0;
  let repaired = false;
  let initial = null;

  if (!testsOk && llm) {
    initial = { ...nodeTest };
    for (let attempt = 0; attempt < MAX_REPAIR_ATTEMPTS; attempt++) {
      const fixed = await repairTests(llm, {
        targetSource: targetSrcPath,
        testsPath,
        stderr: (nodeTest.stderr || "") + "\n" + (nodeTest.stdout || ""),
        attempt,
      });
      writeFileSync(testsPath, fixed, "utf8");
      nodeTest = await runNodeTest(workdir, testFiles, {
        timeoutS: cfg.sandbox.nodeTimeoutS,
      });
      testsOk = nodeTest.returncode === 0;
      repaired = true;
      if (testsOk) break;
    }
  }

  let report = null;
  if (testsOk) {
    const { report: rep } = await runStryker({
      workdir,
      targetFile: targetRel,
      testFiles,
      disabledMutators: cfg.mutation.disabledMutators,
      timeoutS: cfg.sandbox.strykerTimeoutS,
    });
    report = rep;
  }

  return {
    index,
    tier,
    tests_path: testsPath,
    pytest_ok: testsOk,
    // Field naming matches Python so the dashboard reads both languages.
    pytest_result: {
      returncode: nodeTest.returncode,
      stdout: nodeTest.stdout,
      stderr: nodeTest.stderr,
      timed_out: nodeTest.timedOut,
    },
    initial_pytest_result: initial
      ? {
          returncode: initial.returncode,
          stdout: initial.stdout,
          stderr: initial.stderr,
          timed_out: initial.timedOut,
        }
      : null,
    report,
    elapsed_s: (Date.now() - t0) / 1000,
    repaired,
    no_codegen_output: false,
    usage: {
      codegen: { calls: 0, prompt_tokens: 0, completion_tokens: 0 },
      planner: { calls: 0, prompt_tokens: 0, completion_tokens: 0 },
      analysis: { calls: 0, prompt_tokens: 0, completion_tokens: 0 },
    },
  };
}

export async function runLoop({ target, workdir, cfg, llm, maxRounds, runNodeTest = defaultRunNodeTest, runStryker = defaultRunStryker }) {
  const result = new LoopResult(workdir);
  const rounds = result.rounds;
  prepareWorkdir(target, workdir);
  const targetSrcPath = path.join(workdir, "target.js");
  const targetRel = "target.js";
  const started = Date.now();
  // Read the target once and pass it through -- previously tier1/2/3 and the
  // classifier each read it, and the classifier read it every LLM call.
  const sourceText = readFileSync(targetSrcPath, "utf8");

  try {
    // -- round 1: T1 --------------------------------------------------
    let before = llm.snapshotUsage();
    const t1 = await generateT1(llm, { targetSource: targetSrcPath, sourceText });
    const t1Path = path.join(workdir, "test_round_1.js");
    writeFileSync(t1Path, t1.source, "utf8");

    if (!hasTests(t1.source) || isTruncated(t1.finishReason)) {
      const r1 = emptyCodegenRound({ index: 1, tier: 1, testsPath: t1Path });
      r1.usage = serializeUsageDelta(llm.usageDelta(before));
      rounds.push(r1);
      persistRound(workdir, r1);
      writeRoundBody(workdir, r1);
      result.stopped_reason = isTruncated(t1.finishReason)
        ? `codegen truncated in round 1 (finish_reason=${t1.finishReason}); raise codegen max_tokens or switch model`
        : "codegen produced no tests in round 1 (raise codegen max_tokens or switch model)";
      return result;
    }

    const accumulatedTests = ["test_round_1.js"];
    const ctx = { workdir, cfg, llm, runNodeTest, runStryker };
    let round = await runRound(ctx, {
      testsPath: t1Path, allTestFiles: accumulatedTests,
      targetRel, index: 1, tier: 1,
    });
    round.usage = serializeUsageDelta(llm.usageDelta(before));
    rounds.push(round);
    persistRound(workdir, round);
    writeRoundBody(workdir, round);

    if (!round.pytest_ok) {
      result.stopped_reason = "node --test failed in round 1";
      return result;
    }
    if (maxRounds <= 1) {
      result.stopped_reason = "max_rounds reached";
      return result;
    }
    if (!round.report || !round.report.survivors.length) {
      result.stopped_reason = "no survivors after round 1";
      return result;
    }

    // -- rounds 2..N: T2, escalating to T3 once on plateau ----------------
    let prevKill = round.report.kill_rate;
    let t3Used = false;
    let nextTier = 2;

    for (let i = 2; i <= maxRounds; i++) {
      if ((Date.now() - started) / 1000 > cfg.loop.wallClockS) {
        result.stopped_reason = "wall-clock budget exceeded";
        return result;
      }

      before = llm.snapshotUsage();
      const prev = rounds[rounds.length - 1];
      const classified = await classifySurvivors({
        llm,
        targetSource: targetSrcPath,
        sourceText,
        survivors: prev.report.survivors,
        cacheDir: path.join(workdir, ".mutagen"),
      });
      const specs = planSpecs(classified);
      const tier = nextTier;
      appendHandoff(workdir, prev.index, {
        nextRoundIndex: i,
        nextTier: tier,
        classified,
        specs,
      });
      if (!specs.length) {
        result.stopped_reason = "no real_gap survivors to plan against";
        return result;
      }

      const gen =
        tier === 2
          ? await generateT2(llm, { targetSource: targetSrcPath, sourceText, specs })
          : await generateT3(llm, { targetSource: targetSrcPath, sourceText, specs });
      if (tier === 3) t3Used = true;

      const testsPath = path.join(workdir, `test_round_${i}.js`);
      writeFileSync(testsPath, gen.source, "utf8");

      if (!hasTests(gen.source) || isTruncated(gen.finishReason)) {
        // Do NOT push into accumulatedTests -- an empty or truncated file
        // must not carry into subsequent rounds' Stryker runs.
        const r = emptyCodegenRound({ index: i, tier, testsPath });
        r.usage = serializeUsageDelta(llm.usageDelta(before));
        rounds.push(r);
        persistRound(workdir, r);
        writeRoundBody(workdir, r);
        result.stopped_reason = isTruncated(gen.finishReason)
          ? `codegen truncated in round ${i} (finish_reason=${gen.finishReason}); raise codegen max_tokens or switch model`
          : `codegen produced no tests in round ${i} (raise codegen max_tokens or switch model)`;
        return result;
      }
      accumulatedTests.push(`test_round_${i}.js`);

      round = await runRound(ctx, {
        testsPath, allTestFiles: accumulatedTests,
        targetRel, index: i, tier,
      });
      round.usage = serializeUsageDelta(llm.usageDelta(before));
      rounds.push(round);
      persistRound(workdir, round);
      writeRoundBody(workdir, round);

      if (!round.pytest_ok) {
        result.stopped_reason = `node --test failed in round ${i}`;
        return result;
      }
      if (!round.report || !round.report.survivors.length) {
        result.stopped_reason = "no survivors remaining";
        return result;
      }

      const delta = round.report.kill_rate - prevKill;
      if (delta < cfg.loop.plateauDelta) {
        if (!t3Used) {
          nextTier = 3;
          prevKill = round.report.kill_rate;
          continue;
        }
        result.stopped_reason =
          `plateau after T3 (delta=${delta.toFixed(3)} < ${cfg.loop.plateauDelta})`;
        return result;
      }
      prevKill = round.report.kill_rate;
      nextTier = 2;
    }

    result.stopped_reason = "max_rounds reached";
    return result;
  } catch (err) {
    // Set stopped_reason before persistence so run.json distinguishes a crash
    // from a fresh workdir. Re-throw so callers still see the error.
    result.stopped_reason = `crashed: ${err?.name || "Error"}: ${err?.message || String(err)}`;
    throw err;
  } finally {
    persistFinal(workdir, result);
  }
}
