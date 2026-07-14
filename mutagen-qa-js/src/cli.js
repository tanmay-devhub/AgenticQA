/**
 * CLI entry point for `mutagen-js`.
 *
 * Two input modes, mirroring the Python side:
 *   run <target.js>              -- generate + score against a local file.
 *   repo <url> --target-path P   -- clone a git URL, then run against P inside.
 *
 * Kept intentionally small: parse args, load config, wire the LLM and loop,
 * pretty-print the outcome. Substantive logic lives in the modules.
 */

import { copyFileSync, existsSync, mkdirSync, readFileSync, realpathSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";

import dotenv from "dotenv";
import yargs from "yargs";
import { hideBin } from "yargs/helpers";

import { getConfig } from "./config.js";
import { LLM } from "./llm/client.js";
import { runLoop } from "./agent/loop.js";
import { cloneRepo, CloneError } from "./repo/clone.js";
import { detectLanguages } from "./repo/detect.js";

function defaultWorkdir(stem) {
  const ts = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 15);
  return path.resolve("runs", `${stem}-${ts}`);
}

function fmtPct(x) {
  return typeof x === "number" ? `${(x * 100).toFixed(1)}%` : "-";
}

function printSummary(result) {
  console.log("\n--- mutagen-js result ---");
  console.log(`stopped: ${result.stopped_reason}`);
  console.log(`workdir: ${result.workdir}`);
  if (!result.rounds.length) {
    console.log("(no rounds executed)");
    return;
  }
  console.log("\nrounds:");
  console.log("  #  tier   tests   killed/total   survived   kill_rate   elapsed_s   tokens(cg)");
  for (const r of result.rounds) {
    const rep = r.report;
    const killedTotal = rep ? `${rep.killed}/${rep.total}` : "-";
    const survived = rep ? String(rep.survived) : "-";
    const kr = rep ? fmtPct(rep.kill_rate) : "-";
    const tokens = r.usage.codegen.prompt_tokens + r.usage.codegen.completion_tokens;
    console.log(
      `  ${String(r.index).padEnd(3)}T${r.tier}    ${r.pytest_ok ? "ok" : "FAIL"}      ${killedTotal.padEnd(14)}${survived.padEnd(11)}${kr.padEnd(12)}${r.elapsed_s.toFixed(1).padEnd(12)}${tokens}`,
    );
  }
  // Prefer the LoopResult accessor when we got one; fall back to a scan for
  // plain-object results (used by orchestration tests with injected loops).
  const finalReport =
    typeof result.finalReport !== "undefined"
      ? result.finalReport
      : [...result.rounds].reverse().find((r) => r.report)?.report;
  if (finalReport) {
    console.log(
      `\nfinal: killed=${finalReport.killed}/${finalReport.total}  ` +
        `survived=${finalReport.survived}  kill_rate=${fmtPct(finalReport.kill_rate)}`,
    );
  }
}

function writeFocus(workdir, focus, focusFile) {
  // Same convention as the Python side: workdir/focus.txt, read by tier1/2/3
  // generate() without threading a parameter through every layer.
  let text = focus;
  if (!text && focusFile) {
    if (!existsSync(focusFile)) {
      console.error(`--focus-file not found: ${focusFile}`);
      process.exit(2);
    }
    text = readFileSync(focusFile, "utf8");
  }
  text = (text || "").trim();
  if (!text) return;
  mkdirSync(workdir, { recursive: true });
  writeFileSync(path.join(workdir, "focus.txt"), text, "utf8");
}

async function driveLoop({ target, workdir, cfg, maxRounds, note }) {
  const llm = new LLM(cfg);
  console.log(`--- mutagen-js run ---`);
  if (note) console.log(note);
  console.log(`target     ${target}`);
  console.log(`workdir    ${workdir}`);
  console.log(`codegen    ${cfg.llm.codegen.model}`);
  console.log(`planner    ${cfg.llm.planner.model}`);
  console.log(`max_rounds ${maxRounds}`);
  console.log();

  const result = await runLoop({ target, workdir, cfg, llm, maxRounds });
  printSummary(result);
  if (!result.rounds.length || !result.rounds[0].pytest_ok) process.exitCode = 2;
  return result;
}

// The T1/T2/T3 prompts instruct the codegen to write ESM
// (`import { … } from './target.js'`). A CommonJS target with
// `module.exports = …` would break every generated test, so reject early.
function assertSupportedJsFile(p) {
  const ext = path.extname(p).toLowerCase();
  if (!/^\.(m?js|jsx)$/.test(ext)) {
    console.error(
      `unsupported target extension ${JSON.stringify(ext)}; only .js/.mjs/.jsx (ESM) are supported today. ` +
      `CommonJS (.cjs) and TypeScript are on the roadmap.`,
    );
    process.exit(2);
  }
}

async function cmdRun(argv) {
  const target = path.resolve(argv.target);
  if (!existsSync(target)) {
    console.error(`target not found: ${target}`);
    process.exit(2);
  }
  assertSupportedJsFile(target);
  const stem = path.basename(target, path.extname(target));
  const workdir = argv.workdir ? path.resolve(argv.workdir) : defaultWorkdir(stem);
  writeFocus(workdir, argv.focus, argv.focusFile);
  await driveLoop({
    target,
    workdir,
    cfg: getConfig(),
    maxRounds: argv.maxRounds,
  });
}

async function cmdRepo(argv) {
  const stem = argv.targetPath
    ? path.basename(argv.targetPath, path.extname(argv.targetPath))
    : "repo";
  const workdir = argv.workdir ? path.resolve(argv.workdir) : defaultWorkdir(stem);
  mkdirSync(workdir, { recursive: true });
  const cloneDir = path.join(workdir, "_repo");

  console.log(`--- mutagen-js repo ---`);
  console.log(`url         ${argv.url}`);
  console.log(`target-path ${argv.targetPath}`);
  console.log(`workdir     ${workdir}`);
  console.log(`cloning...`);
  try {
    await cloneRepo(argv.url, cloneDir);
  } catch (e) {
    if (e instanceof CloneError) {
      console.error(`clone failed: ${e.message}`);
      process.exit(2);
    }
    throw e;
  }

  const langs = detectLanguages(cloneDir);
  const langLine = Object.entries(langs)
    .map(([k, v]) => `${k}=${v}`)
    .join(" ");
  console.log(`languages   ${langLine || "(none)"}`);

  const rel = argv.targetPath.replace(/^[\\/]+/, "");
  const candidate = path.resolve(cloneDir, rel);
  const cloneResolved = path.resolve(cloneDir);
  if (!candidate.startsWith(cloneResolved + path.sep) && candidate !== cloneResolved) {
    console.error(`target path escapes repo root: ${argv.targetPath}`);
    process.exit(2);
  }
  if (!existsSync(candidate) || !statSync(candidate).isFile()) {
    console.error(`target file not found in repo: ${argv.targetPath}`);
    process.exit(2);
  }
  // Resolve symlinks BEFORE checking containment. A repo checkout can carry
  // symlinks pointing outside the tree (e.g. via crafted .gitattributes); the
  // startsWith check on the unresolved path wouldn't catch that.
  let realTarget;
  let realClone;
  try {
    realTarget = realpathSync(candidate);
    realClone = realpathSync(cloneResolved);
  } catch (e) {
    console.error(`could not resolve target path: ${e.message}`);
    process.exit(2);
  }
  if (!realTarget.startsWith(realClone + path.sep) && realTarget !== realClone) {
    console.error(
      `target path escapes repo root after symlink resolution: ${argv.targetPath}`,
    );
    process.exit(2);
  }
  assertSupportedJsFile(realTarget);

  writeFocus(workdir, argv.focus, argv.focusFile);

  // Point the loop directly at the checked-out file. loop.js's prepareWorkdir
  // copies it into workdir/target.js exactly once. No sidecar hop needed.
  await driveLoop({
    target: realTarget,
    workdir,
    cfg: getConfig(),
    maxRounds: argv.maxRounds,
    note: `repo        ${argv.url}\ntarget      ${argv.targetPath}`,
  });
}

export function findEnvUpward(startDir) {
  // Walk parents up to the filesystem root looking for a `.env`. This is what
  // most users expect when they run mutagen-js from a subdirectory of their
  // project instead of the package root.
  let dir = startDir;
  while (true) {
    const candidate = path.join(dir, ".env");
    if (existsSync(candidate)) return candidate;
    const parent = path.dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

export async function main(argv = hideBin(process.argv)) {
  const envPath = findEnvUpward(process.cwd());
  dotenv.config(envPath ? { path: envPath } : {});

  await yargs(argv)
    .command(
      "run <target>",
      "Generate tests for TARGET and run the mutation loop",
      (y) =>
        y
          .positional("target", { type: "string", describe: "path to a .js source file" })
          .option("workdir", { alias: "w", type: "string", describe: "output directory" })
          .option("focus", { type: "string", describe: "plain-English priority passed to codegen" })
          .option("focus-file", { type: "string", describe: "read focus text from this file" })
          .option("max-rounds", {
            alias: "r", type: "number", default: 3, describe: "cap on loop iterations",
            coerce: (v) => {
              if (!Number.isInteger(v) || v < 1 || v > 10) {
                throw new Error("--max-rounds must be an integer in [1, 10]");
              }
              return v;
            },
          }),
      cmdRun,
    )
    .command(
      "repo <url>",
      "Clone URL and run the mutation loop against --target-path inside it",
      (y) =>
        y
          .positional("url", { type: "string", describe: "https:// or file:// git URL" })
          .option("target-path", { alias: "t", type: "string", demandOption: true, describe: "relative path to the .js file inside the repo" })
          .option("workdir", { alias: "w", type: "string" })
          .option("focus", { type: "string", describe: "plain-English priority passed to codegen" })
          .option("focus-file", { type: "string", describe: "read focus text from this file" })
          .option("max-rounds", {
            alias: "r", type: "number", default: 3,
            coerce: (v) => {
              if (!Number.isInteger(v) || v < 1 || v > 10) {
                throw new Error("--max-rounds must be an integer in [1, 10]");
              }
              return v;
            },
          }),
      cmdRepo,
    )
    .command("version", "Print version", () => {}, async () => {
      const { readFileSync } = await import("node:fs");
      const { fileURLToPath } = await import("node:url");
      const pkgPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "package.json");
      const pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
      console.log(pkg.version);
    })
    .demandCommand(1)
    .strict()
    .help()
    .parseAsync();
}
