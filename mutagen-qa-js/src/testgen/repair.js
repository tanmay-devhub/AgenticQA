/**
 * Two-shot repair for a broken generated test module.
 *
 * If the tests we generated fail to run under `node --test` (import failure,
 * syntax error, stray backtick from the model), Stryker then can't score
 * anything. Give the model up to TWO chances to fix its own output:
 *
 *     attempt 0: same temperature as codegen. Usually catches syntax + import
 *                bugs, occasional off-by-one in assertions.
 *     attempt 1: escalates codegen temperature by +0.3 and re-frames the
 *                prompt as "your fix still broke; try a different tactic."
 *
 * Kept to two shots: if both fail the model's mental model of the target is
 * genuinely off and we should surface that instead of burning tokens.
 */

import { readFileSync } from "node:fs";

import { stripFences } from "./util.js";

export const MAX_REPAIR_ATTEMPTS = 2;
const TEMP_BUMP_PER_ATTEMPT = 0.3;

const SYSTEM =
  "You are a senior JavaScript engineer fixing a broken node:test module. The " +
  "module below fails to run under `node --test`. Analyze the error, then " +
  "return the ENTIRE fixed module source. Rules: import target as `import { ... } " +
  "from './target.js';`, use 'node:test' + 'node:assert/strict' only, assert " +
  "only on error CONSTRUCTOR (never on message text). Return ONLY the JavaScript " +
  "source -- no prose, no markdown fences.";

const ATTEMPT1_HINT =
  "\n\nNOTE: an earlier repair attempt still failed. Do NOT reuse the same " +
  "expected values -- re-read the target's docstring carefully and revise your " +
  "assertions against what it actually documents, then return the whole fixed module.";

function userTemplate({ source, tests, stderr, hint }) {
  return `Target module (target.js):

\`\`\`javascript
${source}
\`\`\`

Current broken test module:

\`\`\`javascript
${tests}
\`\`\`

node --test output (stderr):

\`\`\`
${(stderr || "").slice(-4000)}
\`\`\`

Return the ENTIRE fixed test module source now.${hint}`;
}

export async function repair(llm, { targetSource, testsPath, stderr, attempt = 0 }) {
  const source = readFileSync(targetSource, "utf8");
  const tests = readFileSync(testsPath, "utf8");
  const hint = attempt >= 1 ? ATTEMPT1_HINT : "";

  // Best-effort temperature bump on the codegen role, restored via try/finally
  // so a caller re-using the LLM instance sees config unchanged. FakeLLM in
  // tests has no _config -- skip cleanly in that case.
  const role = llm?._config?.llm?.codegen;
  const originalTemp = role?.temperature;
  if (role && attempt >= 1 && originalTemp != null) {
    role.temperature = Math.min(1.5, originalTemp + TEMP_BUMP_PER_ATTEMPT * attempt);
  }
  try {
    const resp = await llm.complete("codegen", {
      system: SYSTEM,
      user: userTemplate({ source, tests, stderr, hint }),
    });
    return stripFences(resp.text);
  } finally {
    if (role && originalTemp != null) role.temperature = originalTemp;
  }
}
