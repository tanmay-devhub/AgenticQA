/**
 * Tier 3: property-based tests via fast-check (JS's Hypothesis).
 *
 * Escalation target for the loop: reached after Tier-2 example-based tests
 * plateau. Property tests state invariants that fast-check probes with a
 * diverse set of inputs -- often the only way to kill arithmetic-and-return
 * survivors that a hand-crafted case list keeps missing.
 *
 * fast-check runs inside node:test via `fc.assert(fc.property(...))`.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { focusDirective, readFocus, stripFences } from "./util.js";
import { renderSpecs } from "./tier2.js";

const SYSTEM =
  "You are a senior JavaScript QA engineer writing PROPERTY-BASED tests with " +
  "fast-check. Previous rounds of example-based tests have plateaued: some " +
  "mutations still survive. Your job is to write invariants that fast-check " +
  "can falsify on at least one generated input. Rules: use Node's built-in " +
  "test runner ('node:test'); import fast-check as `import fc from " +
  "'fast-check';`; assert only on return values or thrown error CONSTRUCTOR " +
  "(never on message text); keep it lean (<= 8 property tests). Return ONLY " +
  "valid ES module source, no prose, no markdown fences.";

function userTemplate(source, specsText) {
  return `Target module source (file: target.js):

\`\`\`javascript
${source}
\`\`\`

Example-based tests have plateaued. The following mutations still survive.
Write a fast-check-driven node:test module \`test_round_N.js\` whose properties
would be falsified by at least one of these mutations.

${specsText}

Rules:
- Pick fast-check arbitraries (\`fc.integer\`, \`fc.string\`, \`fc.array\`, ...) whose range
  actually exercises the surviving mutation. Constrain with \`min\`, \`max\`, or a
  regex alphabet so tests don't drift into undefined behavior.
- Prefer invariants that would OBSERVABLY differ under the mutation:
  round-trips, bounds, monotonicity, algebraic identities.
- Each property test looks like \`test('name', () => { fc.assert(fc.property(arb, x => ...)); });\`.
- Keep total test count under 10.
- Return ONLY the JavaScript source, nothing else.
`;
}

export async function generateT3(llm, { targetSource, sourceText, specs }) {
  const source = sourceText ?? readFileSync(targetSource, "utf8");
  const focus = readFocus(path.dirname(targetSource));
  const user = focusDirective(focus) + userTemplate(source, renderSpecs(specs));
  const resp = await llm.complete("codegen", { system: SYSTEM, user });
  return { source: stripFences(resp.text), finishReason: resp.finishReason };
}
