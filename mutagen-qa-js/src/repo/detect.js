/**
 * Language detection by filename extension.
 *
 * Not a full linguist-style classifier. For deciding which mutation pipeline
 * to run against a cloned repo, an extension table is efficient,
 * deterministic, and easy to override when a language grows.
 *
 * Skip list stays wide because a false negative (missing one .py inside
 * node_modules -- there shouldn't be one) is cheaper than a false positive
 * (counting 30k .js files in node_modules as user code).
 */

import { readdirSync, statSync } from "node:fs";
import path from "node:path";

export const LANG_BY_EXT = {
  ".py": "python",
  ".pyi": "python",
  ".js": "javascript",
  ".mjs": "javascript",
  ".cjs": "javascript",
  ".jsx": "javascript",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".java": "java",
  ".cs": "csharp",
  ".cpp": "cpp",
  ".cc": "cpp",
  ".cxx": "cpp",
  ".hpp": "cpp",
  ".hh": "cpp",
  ".hxx": "cpp",
};

const SKIP_DIRS = new Set([
  ".git", ".hg", ".svn",
  "node_modules", ".pnpm-store", ".yarn",
  "__pycache__", ".venv", "venv", "env",
  "build", "dist", "target", "bin", "obj", "out",
  ".next", ".nuxt", ".turbo", ".svelte-kit",
  ".gradle", ".idea", ".vscode",
  "coverage", "htmlcov", ".nyc_output",
  ".mypy_cache", ".pytest_cache", ".ruff_cache", ".hypothesis",
  ".stryker-tmp", "StrykerOutput", "pit-reports", "mull-report",
  "CMakeFiles", "cmake-build-debug", "cmake-build-release",
]);

function* walkSourceFiles(root) {
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    let children;
    try {
      children = readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const child of children) {
      // Symlinks inside a cloned repo can loop -- skip.
      if (child.isSymbolicLink()) continue;
      const full = path.join(current, child.name);
      if (child.isDirectory()) {
        if (SKIP_DIRS.has(child.name) || child.name.startsWith(".")) continue;
        stack.push(full);
      } else if (child.isFile()) {
        yield full;
      }
    }
  }
}

export function detectLanguages(repoPath) {
  const resolved = path.resolve(repoPath);
  try {
    if (!statSync(resolved).isDirectory()) return {};
  } catch {
    return {};
  }

  const counts = {};
  for (const p of walkSourceFiles(resolved)) {
    const ext = path.extname(p).toLowerCase();
    const lang = LANG_BY_EXT[ext];
    if (!lang) continue;
    counts[lang] = (counts[lang] || 0) + 1;
  }
  // Sort by descending count so the first key is the dominant language.
  return Object.fromEntries(
    Object.entries(counts).sort(([ak, av], [bk, bv]) => bv - av || ak.localeCompare(bk)),
  );
}
