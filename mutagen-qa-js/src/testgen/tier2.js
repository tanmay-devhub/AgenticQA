/**
 * Tier 2: boundary / error-path / negative tests driven by planner specs.
 *
 * Same shape as tier1.generate: one LLM call, returns a runnable node:test
 * module string. Prompt references the specific survivors + the technique
 * hint so the model targets the gaps that still exist.
 */

import { readFileSync } from "node:fs";
import path from "node:path";

import { focusDirective, readFocus, stripFences } from "./util.js";

const SYSTEM =
  "You are a senior JavaScript QA engineer writing a follow-up test module using " +
  "Node's built-in test runner ('node:test') and 'node:assert/strict'. A previous " +
  "round has already covered the happy paths. Your job is to KILL the specific " +
  "mutations that survived. Keep the module lean; assert on error CONSTRUCTOR " +
  "only (never on message text). Return ONLY valid ES module source for a single " +
  "test file. Import via `import { <symbols> } from './target.js';`.";

// Strip `--- <file>`, `+++ <file>`, and `@@ … @@` header lines from a
// unified diff. Every survivor in a cluster carries the same file header,
// so repeating it eats prompt tokens without adding information.
function trimDiffHeaders(diff) {
  return (diff || "")
    .split("\n")
    .filter((ln) => !/^(?:---|\+\+\+)\s/.test(ln) && !/^@@\s/.test(ln))
    .join("\n")
    .trim();
}

export function renderSpecs(specs) {
  const blocks = [];
  specs.forEach((spec, i) => {
    const survLines = spec.survivors.map((m) => {
      const loc = m.line != null ? `line ${m.line}` : "line ?";
      const diff = trimDiffHeaders(m.diff);
      return `    - id=${m.id} (${loc}, kind=${m.kind}):\n\`\`\`diff\n${diff}\n\`\`\``;
    });
    const survBlock = survLines.length ? survLines.join("\n") : "    (no diffs available)";
    const covHint = spec.uncovered_lines?.length
      ? `\n  Coverage: lines ${JSON.stringify(spec.uncovered_lines)} in this region are NOT executed by any test yet -- prioritize inputs that exercise them.`
      : "";
    blocks.push(
      `Spec ${i + 1}: function \`${spec.function || "?"}\` in \`${spec.file}\`\n` +
        `  Dominant mutation kind: ${spec.dominant_kind}\n` +
        `  Suggested technique: ${spec.technique_hint}${covHint}\n` +
        `  Surviving mutations to kill:\n${survBlock}`,
    );
  });
  return blocks.join("\n\n");
}

function userTemplate(source, specsText) {
  return `Target module source (file: target.js):

\`\`\`javascript
${source}
\`\`\`

The following mutations survived the previous round of tests. Write a
node:test module \`test_round_N.js\` that kills them. For each spec, add the
smallest set of well-chosen tests that make the surviving mutation observable
in the return value or the thrown error CONSTRUCTOR.

${specsText}

Rules:
- Do NOT re-test happy paths already covered elsewhere; focus on the specs above.
- Use \`assert.throws(() => fn(bad), TypeError)\` for error paths; assert only the constructor.
- Prefer table-driven tests (one loop over cases) over many single-case tests.
- Keep total test count under 15.
- Return ONLY the JavaScript source, nothing else.
`;
}

export async function generateT2(llm, { targetSource, sourceText, specs }) {
  const source = sourceText ?? readFileSync(targetSource, "utf8");
  const focus = readFocus(path.dirname(targetSource));
  const user = focusDirective(focus) + userTemplate(source, renderSpecs(specs));
  const resp = await llm.complete("codegen", { system: SYSTEM, user });
  return { source: stripFences(resp.text), finishReason: resp.finishReason };
}
