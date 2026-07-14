import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import os from "node:os";

import { cloneRepo, CloneError } from "../src/repo/clone.js";
import { runSubprocess } from "../src/sandbox/executor.js";

function tmpdir() {
  return mkdtempSync(path.join(os.tmpdir(), "mutagen-js-clone-"));
}

test("clone: rejects empty url", async () => {
  await assert.rejects(() => cloneRepo("", tmpdir()), CloneError);
  await assert.rejects(() => cloneRepo("   ", tmpdir()), CloneError);
});

test("clone: rejects non-URL strings", async () => {
  await assert.rejects(() => cloneRepo("not a url", tmpdir()), CloneError);
  await assert.rejects(() => cloneRepo("owner/repo", tmpdir()), CloneError);
});

test("clone: rejects disallowed schemes", async () => {
  await assert.rejects(() => cloneRepo("git://gh.com/x/y.git", tmpdir()), CloneError);
  await assert.rejects(() => cloneRepo("ssh://gh.com/x/y.git", tmpdir()), CloneError);
  await assert.rejects(() => cloneRepo("javascript:alert(1)", tmpdir()), CloneError);
});

test("clone: rejects non-empty destination", async () => {
  const dest = tmpdir();
  writeFileSync(path.join(dest, "existing"), "x", "utf8");
  await assert.rejects(
    () => cloneRepo("https://example.com/x.git", dest),
    /destination is not empty/,
  );
});

test("clone: shallow-clones a file:// URL end-to-end", async () => {
  // Build a tiny bare repo locally so we can test the real clone path without
  // network. `git init`, one commit, then clone via file:// URL.
  const srcRepo = tmpdir();
  const workDir = path.join(srcRepo, "src");
  mkdirSync(workDir, { recursive: true });
  writeFileSync(path.join(workDir, "hello.js"), "export const hi = 1;\n", "utf8");

  const stepArgs = [
    ["git", "init", "-q", "-b", "main"],
    ["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "src/hello.js"],
    ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
  ];
  for (const args of stepArgs) {
    const res = await runSubprocess(args, { cwd: srcRepo, timeoutS: 30 });
    assert.equal(res.returncode, 0, `${args.join(" ")} failed: ${res.stderr}`);
  }

  const dest = tmpdir();
  // Empty tmpdir means the clone-dest guard passes.
  const url = `file://${srcRepo.replace(/\\/g, "/")}`;
  const cloned = await cloneRepo(url, dest);
  assert.equal(cloned, path.resolve(dest));
  assert.ok(existsSync(path.join(dest, "src", "hello.js")));
  assert.ok(existsSync(path.join(dest, ".git")));
});
