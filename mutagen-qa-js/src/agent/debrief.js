/**
 * Per-round debrief writer. JS port of agent/debrief.py.
 *
 * Two passes:
 *   1. writeRoundBody: after a round finishes -- test run / repair / mutation
 *      sections.
 *   2. appendHandoff: at the start of the next round -- classifier verdicts +
 *      planner specs, so each debrief tells the full "these survived -> we
 *      classified them like this -> next round will use this technique" story.
 *
 * The last round's debrief has NO handoff section on purpose (no next round);
 * the loop's stop reason is written into run.json alongside.
 */

import { appendFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const MAX_FAILURES_LISTED = 20;
const MAX_FAILURE_REASON_LEN = 200;

// node --test emits TAP-13 with a YAML block per failure:
//   not ok 1 - test name
//     ---
//     duration_ms: 3.2
//     failureType: 'testCodeFailure'
//     error: 'Expected values to be strictly equal:\n1 !== 2'
//     code: 'ERR_ASSERTION'
//     stack: |
//       ...
//     ...
// We capture the name from the `not ok` line and the `error:` field from the
// YAML that follows. Anything else in the block is noise for the debrief.
const NOT_OK_RE = /^not ok \d+\s+-\s+(.+)$/gm;
const ERROR_YAML_RE = /^\s{2,}error:\s+(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|(.+))$/m;

function parseFailingTests(stdout) {
  const out = [];
  const text = stdout || "";
  let m;
  NOT_OK_RE.lastIndex = 0;
  while ((m = NOT_OK_RE.exec(text)) && out.length < MAX_FAILURES_LISTED) {
    const name = m[1].split("#")[0].trim().slice(0, MAX_FAILURE_REASON_LEN);
    // Scan the YAML block that follows this `not ok` line: from here up to
    // the next `not ok` / `ok` line or `1..N` plan line.
    const rest = text.slice(NOT_OK_RE.lastIndex);
    const nextBlock = rest.search(/^(?:not )?ok \d+|^1\.\.\d+/m);
    const block = nextBlock >= 0 ? rest.slice(0, nextBlock) : rest;
    const em = ERROR_YAML_RE.exec(block);
    let reason = "";
    if (em) {
      const raw = em[1] ?? em[2] ?? em[3] ?? "";
      // Unescape common YAML escapes and collapse newline placeholders.
      reason = raw
        .replace(/\\n/g, " ")
        .replace(/\\'/g, "'")
        .replace(/\\"/g, '"')
        .trim()
        .slice(0, MAX_FAILURE_REASON_LEN);
    }
    out.push({ name, reason });
  }
  return out;
}

function roundDebriefPath(workdir, index) {
  return path.join(workdir, `round_${index}_debrief.md`);
}

function fmtPct(x) {
  return typeof x === "number" ? `${(x * 100).toFixed(1)}%` : "-";
}

export function writeRoundBody(workdir, r) {
  const lines = [];
  lines.push(`# Round ${r.index} — Tier ${r.tier}`);
  lines.push("");
  lines.push(`- **elapsed:** ${r.elapsed_s.toFixed(1)}s`);
  lines.push(`- **tests file:** \`${path.basename(r.tests_path)}\``);
  lines.push("");

  if (r.no_codegen_output) {
    lines.push("## codegen");
    lines.push("");
    lines.push(
      "The codegen model returned no test functions. Typical cause: the model " +
        "exhausted its `max_tokens` budget on hidden reasoning tokens before " +
        "emitting any test code. `node --test` and Stryker were **not** invoked " +
        "for this round -- running them against an empty test file would " +
        "silently rediscover the previous round's tests and make the failed " +
        "round look successful.",
    );
    lines.push("");
    const p = roundDebriefPath(workdir, r.index);
    writeFileSync(p, lines.join("\n"), "utf8");
    return p;
  }

  // -- test run ----------------------------------------------------------
  lines.push("## test run");
  lines.push("");
  if (r.initial_pytest_result) {
    const initialFailures = parseFailingTests(r.initial_pytest_result.stdout);
    lines.push(`Initial run: **FAILED** (rc=${r.initial_pytest_result.returncode}).`);
    if (initialFailures.length) {
      lines.push("");
      lines.push(`First ${initialFailures.length} failing test(s):`);
      lines.push("");
      for (const f of initialFailures) {
        lines.push(`- \`${f.name}\`` + (f.reason ? ` — ${f.reason}` : ""));
      }
    } else {
      lines.push("");
      lines.push(
        "No `not ok` markers were extractable from the node --test output -- " +
          "the runner likely failed before any test ran. Check " +
          "`initial_pytest_result.stderr` for the traceback.",
      );
    }
  } else {
    lines.push("Initial run: **passed** (no repair needed).");
  }
  lines.push("");

  // -- repair ------------------------------------------------------------
  lines.push("## repair");
  lines.push("");
  if (r.repaired) {
    const outcome = r.pytest_ok ? "passed" : "still failed";
    lines.push(
      `Repair was invoked. Final test run: **${outcome}** (rc=${r.pytest_result.returncode}).`,
    );
    if (!r.pytest_ok) {
      const finalFailures = parseFailingTests(r.pytest_result.stdout);
      if (finalFailures.length) {
        lines.push("");
        lines.push("Still failing after repair:");
        lines.push("");
        for (const f of finalFailures) {
          lines.push(`- \`${f.name}\`` + (f.reason ? ` — ${f.reason}` : ""));
        }
      }
    }
  } else {
    lines.push("No repair attempted.");
  }
  lines.push("");

  // -- Stryker -----------------------------------------------------------
  lines.push("## Stryker");
  lines.push("");
  if (!r.report) {
    lines.push("Stryker was **skipped** because `node --test` didn't reach a passing state.");
  } else {
    const rep = r.report;
    lines.push(
      `Killed **${rep.killed} / ${rep.total}** (kill rate ${fmtPct(rep.kill_rate)}). Survived: **${rep.survived}**.`,
    );
    if (rep.survivors.length) {
      lines.push("");
      lines.push("### surviving mutants");
      lines.push("");
      for (const m of rep.survivors) {
        const loc = m.line != null ? ` (line ${m.line})` : "";
        lines.push(`**\`${m.id}\`** — kind=\`${m.kind}\`${loc}`);
        if (m.diff) {
          lines.push("");
          lines.push("```diff");
          lines.push(m.diff.trim());
          lines.push("```");
        }
        lines.push("");
      }
    }
  }

  const p = roundDebriefPath(workdir, r.index);
  writeFileSync(p, lines.join("\n"), "utf8");
  return p;
}

export function appendHandoff(workdir, roundIndex, { nextRoundIndex, nextTier, classified, specs }) {
  const p = roundDebriefPath(workdir, roundIndex);
  const verdicts = { real_gap: 0, equivalent: 0, message_noise: 0 };
  for (const c of classified) verdicts[c.verdict] = (verdicts[c.verdict] || 0) + 1;

  const lines = ["", `## handoff to round ${nextRoundIndex}`, ""];
  lines.push(`Next tier: **T${nextTier}**.`);
  lines.push("");
  lines.push("Classifier verdicts on this round's survivors:");
  lines.push("");
  lines.push(`- \`real_gap\`: **${verdicts.real_gap}** (fed to next generator)`);
  lines.push(`- \`equivalent\`: **${verdicts.equivalent}** (skipped -- unkillable)`);
  lines.push(`- \`message_noise\`: **${verdicts.message_noise}** (skipped -- non-behavioral)`);
  lines.push("");

  if (specs.length) {
    lines.push(`Round ${nextRoundIndex} will target ${specs.length} cluster(s):`);
    lines.push("");
    for (const s of specs) {
      const fn = s.function ? `::${s.function}` : "";
      lines.push(
        `- **\`${s.file}${fn}\`** — ${s.survivors.length} survivor(s), dominant kind=\`${s.dominant_kind}\`.`,
      );
      lines.push(`  - technique: ${s.technique_hint}`);
      if (s.uncovered_lines?.length) {
        lines.push(`  - uncovered lines in span: ${JSON.stringify(s.uncovered_lines)}`);
      }
    }
    lines.push("");
  } else {
    lines.push(
      "Planner produced no specs -- either no real_gap survivors remain or " +
        "every cluster was empty after filtering. The loop will stop here.",
    );
    lines.push("");
  }

  appendFileSync(p, lines.join("\n"), "utf8");
  return p;
}

export const _internal = { parseFailingTests };
