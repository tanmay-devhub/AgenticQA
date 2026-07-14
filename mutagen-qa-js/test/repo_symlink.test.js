import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, symlinkSync, writeFileSync, mkdirSync, realpathSync } from "node:fs";
import path from "node:path";
import os from "node:os";

function tmpdir() {
  return mkdtempSync(path.join(os.tmpdir(), "mutagen-js-symlink-"));
}

function trySymlink(target, linkPath) {
  try {
    symlinkSync(target, linkPath);
    return true;
  } catch {
    return false; // Windows non-admin without Developer Mode.
  }
}

test("realpathSync unmasks a symlink that escapes the clone dir (guarded by cmdRepo)", (t) => {
  const scratch = tmpdir();
  const outside = path.join(scratch, "outside.js");
  writeFileSync(outside, "export const oops = 1;\n", "utf8");
  const cloneDir = path.join(scratch, "clone");
  mkdirSync(cloneDir);
  const link = path.join(cloneDir, "sneaky.js");
  if (!trySymlink(outside, link)) {
    t.skip("symlink creation not permitted; guard verified by inspection");
    return;
  }

  const cloneReal = realpathSync(cloneDir);
  const targetReal = realpathSync(link);
  // This is exactly the check cli.js `cmdRepo` performs. Prior to the fix
  // (using `path.resolve` alone) the check would have passed because
  // `link` sits inside cloneDir; after realpathSync it correctly points
  // outside.
  assert.ok(!targetReal.startsWith(cloneReal + path.sep),
    "symlink target must not be inside the clone dir after resolution");
});
