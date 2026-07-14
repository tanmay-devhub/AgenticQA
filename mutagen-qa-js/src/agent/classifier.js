/**
 * Survivor classifier. JS port of agent/classifier.py.
 *
 * Feeds each unique survivor diff to the planner LLM; caches verdicts by
 * SHA-1 in <cache_dir>/classifier_cache.json so multi-round loops pay Gemini
 * once per unique diff across the whole run.
 *
 * Verdicts:
 *   real_gap       -- fed to the next-round generator.
 *   equivalent     -- semantically identical to original; unkillable.
 *   message_noise  -- only the wording of an error message changed.
 */

import crypto from "node:crypto";
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import path from "node:path";

const SYSTEM =
  "You are a mutation-testing analyst. You are shown the ORIGINAL source of a " +
  "JavaScript function and a UNIFIED DIFF of a single mutation applied to it. " +
  "Decide, from behavior alone, whether the mutated code differs OBSERVABLY " +
  "from the original for at least one legal input. Respond with a single line " +
  'of JSON: {"verdict":"real_gap"|"equivalent"|"message_noise","reason":"<one short sentence>"}. ' +
  "Definitions: real_gap = there exists a legal input for which original and " +
  "mutant produce different return values, throw different error CONSTRUCTORS, " +
  "or diverge in any externally visible way. equivalent = for every legal input " +
  "both versions produce the same return value and the same thrown constructor " +
  "(message wording may differ). message_noise = the only difference is the " +
  "wording of an error message, log string, or template interpolated into a " +
  "message. Output ONLY the JSON object; no prose, no code fences.";

function userTemplate(source, diff) {
  return `Original source (target.js):

\`\`\`javascript
${source}
\`\`\`

Mutation diff:

\`\`\`diff
${diff}
\`\`\`

Return the JSON verdict now.`;
}

function diffHash(diff) {
  return crypto.createHash("sha1").update(diff, "utf8").digest("hex");
}

function loadCache(cacheDir) {
  const p = path.join(cacheDir, "classifier_cache.json");
  if (!existsSync(p)) return {};
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return {};
  }
}

function saveCache(cacheDir, data) {
  // Atomic write via rename: two loops writing the same workdir won't clobber
  // each other's cache. Windows rename is atomic when both paths are on the
  // same volume, which they are here (both under cacheDir).
  mkdirSync(cacheDir, { recursive: true });
  const final = path.join(cacheDir, "classifier_cache.json");
  const tmp = final + `.tmp.${process.pid}`;
  writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
  renameSync(tmp, final);
}

function parseVerdict(text) {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end === -1 || end < start) {
    return { verdict: "real_gap", reason: "unparseable response; defaulted to real_gap" };
  }
  let obj;
  try {
    obj = JSON.parse(text.slice(start, end + 1));
  } catch {
    return { verdict: "real_gap", reason: "invalid JSON; defaulted to real_gap" };
  }
  const v = obj.verdict;
  const reason = String(obj.reason ?? "").slice(0, 200);
  if (v !== "real_gap" && v !== "equivalent" && v !== "message_noise") {
    return { verdict: "real_gap", reason: `unknown verdict ${JSON.stringify(v)}; defaulted to real_gap` };
  }
  return { verdict: v, reason };
}

// Cap concurrent planner calls so we don't hammer the API. 4 matches
// Gemini's free-tier RPS budget with comfortable headroom.
const DEFAULT_CONCURRENCY = 4;

async function runWithConcurrency(items, limit, worker) {
  // Deliberately simple: a shared cursor + N in-flight promises. p-limit would
  // work too, but adding a dep for 12 lines isn't worth it.
  const results = new Array(items.length);
  let cursor = 0;
  async function next() {
    while (true) {
      const i = cursor++;
      if (i >= items.length) return;
      results[i] = await worker(items[i], i);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, next));
  return results;
}

export async function classifySurvivors({ llm, targetSource, sourceText, survivors, cacheDir, concurrency = DEFAULT_CONCURRENCY }) {
  if (!survivors.length) return [];
  const source = sourceText ?? readFileSync(targetSource, "utf8");
  const cache = loadCache(cacheDir);
  let dirty = false;

  // First pass: serve cache hits and no-diff defaults without any LLM call.
  // Second pass: parallelize the remaining LLM calls with a concurrency cap.
  const outputs = new Array(survivors.length);
  const pending = [];
  survivors.forEach((m, i) => {
    if (!m.diff) {
      outputs[i] = { mutant: m, verdict: "real_gap", reason: "no diff available" };
      return;
    }
    const h = diffHash(m.diff);
    if (cache[h]) {
      outputs[i] = { mutant: m, verdict: cache[h].verdict || "real_gap", reason: cache[h].reason || "" };
      return;
    }
    pending.push({ index: i, mutant: m, hash: h });
  });

  await runWithConcurrency(pending, concurrency, async (item) => {
    let text;
    try {
      const resp = await llm.complete("planner", {
        system: SYSTEM, user: userTemplate(source, item.mutant.diff),
      });
      text = resp.text;
    } catch (err) {
      // Rate limit / network / auth: default to real_gap and don't cache the
      // failure so a later retry can still get a real verdict.
      const reason = `planner call failed: ${err?.name || "Error"}; defaulted to real_gap`;
      outputs[item.index] = { mutant: item.mutant, verdict: "real_gap", reason: reason.slice(0, 200) };
      return;
    }
    const { verdict, reason } = parseVerdict(text);
    cache[item.hash] = { verdict, reason };
    dirty = true;
    outputs[item.index] = { mutant: item.mutant, verdict, reason };
  });

  if (dirty) saveCache(cacheDir, cache);
  return outputs;
}

// Test seams.
export const _internal = { parseVerdict, diffHash };
