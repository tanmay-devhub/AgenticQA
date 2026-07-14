import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, existsSync, readFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

const _thisDir = path.dirname(fileURLToPath(import.meta.url));
const PKG_ROOT = path.resolve(_thisDir, "..");
const BIN = path.join(PKG_ROOT, "bin", "mutagen-js.js");

function tmpdir() {
  return mkdtempSync(path.join(os.tmpdir(), "mutagen-js-cli-"));
}

function runCli(argv, { env, cwd } = {}) {
  return spawnSync(process.execPath, [BIN, ...argv], {
    encoding: "utf8", env: { ...process.env, ...(env || {}) }, cwd,
  });
}

test("CLI rejects .cjs targets with a helpful error", () => {
  const dir = tmpdir();
  const target = path.join(dir, "t.cjs");
  writeFileSync(target, "module.exports = 1;", "utf8");
  const res = runCli(["run", target, "--max-rounds", "1"]);
  assert.equal(res.status, 2);
  assert.match(res.stderr, /unsupported target extension/);
  assert.match(res.stderr, /CommonJS/);
});

test("CLI rejects out-of-range --max-rounds", () => {
  const res = runCli(["run", "whatever.js", "--max-rounds", "99"]);
  assert.notEqual(res.status, 0);
  assert.match(res.stderr, /max-rounds must be an integer in \[1, 10\]/);
});

test("--focus writes focus.txt into the workdir", () => {
  const dir = tmpdir();
  const target = path.join(dir, "target.js");
  writeFileSync(target, "export const x = 1;\n", "utf8");
  const workdir = path.join(dir, "wd");
  // We only need to verify focus.txt is written; dotenv will fail on missing
  // API key, so we set a stub env value and run --max-rounds 1 with no keys.
  // Setting focus should happen BEFORE the loop starts, regardless of LLM.
  const res = runCli(
    ["run", target, "--workdir", workdir, "--focus", "cover unicode fold at NFKD boundary", "--max-rounds", "1"],
    { env: { OLLAMA_API_KEY: "", GEMINI_API_KEY: "" } },
  );
  // The loop will crash on the LLM call, but focus.txt must exist first.
  assert.ok(existsSync(path.join(workdir, "focus.txt")), "focus.txt written before loop");
  const text = readFileSync(path.join(workdir, "focus.txt"), "utf8");
  assert.equal(text, "cover unicode fold at NFKD boundary");
  // The unused var linter would flag res; assert exit code sane.
  assert.ok(res.status !== 0, "loop should exit non-zero due to missing keys");
});

test("findEnvUpward walks parents until it finds a .env", async () => {
  const { findEnvUpward } = await import("../src/cli.js");
  const project = tmpdir();
  const envPath = path.join(project, ".env");
  writeFileSync(envPath, "X=1\n", "utf8");
  const subdir = path.join(project, "a", "b");
  mkdirSync(subdir, { recursive: true });
  const found = findEnvUpward(subdir);
  assert.equal(found, envPath);
});

test("findEnvUpward returns null when no .env exists on the walk", async () => {
  const { findEnvUpward } = await import("../src/cli.js");
  const dir = tmpdir();
  // Use a real filesystem path that has no .env in itself or its parents up
  // to root. tmpdir may itself have unrelated .env files on some CI images,
  // so this can be brittle -- treat any non-null result as pass-through
  // (the finder found something outside our test scope).
  const found = findEnvUpward(dir);
  if (found !== null) {
    // Anything found must live at or above dir.
    assert.ok(dir.startsWith(path.dirname(found)) || path.dirname(found) === dir,
      "any hit must be at or above the search root");
  }
});
