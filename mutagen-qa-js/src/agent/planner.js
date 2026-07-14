/**
 * Rule-based planner. JS port of agent/planner.py.
 *
 * Consumes classifier verdicts, drops non-`real_gap` survivors, clusters the
 * rest, and emits one TestSpec per cluster. Each spec carries the dominant
 * mutation kind and a technique hint that the T2/T3 generator appends to its
 * prompt.
 *
 * Clustering key difference from the Python side: Stryker mutant IDs are
 * bare numeric strings ("1", "2", ...) with no function name embedded (mutmut
 * IDs like `target.foo__mutmut_3` did carry the function name). We cluster by
 * (file, line-window) instead -- close enough for the generator's purposes.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// Loaded from the cross-language schema so Python and JS share the exact same
// technique text. Path is relative to this file, walks up to <repo>/shared/.
const _thisDir = path.dirname(fileURLToPath(import.meta.url));
const _schemaPath = path.resolve(_thisDir, "..", "..", "..", "shared", "schema", "technique_by_kind.json");
const _rawSchema = JSON.parse(readFileSync(_schemaPath, "utf8"));
// Drop JSON-schema metadata keys (`$comment`, etc.) so lookups always hit
// only real kinds.
const TECHNIQUE_BY_KIND = Object.fromEntries(
  Object.entries(_rawSchema).filter(([k]) => !k.startsWith("$")),
);

// A line-window of +/- LINE_CLUSTER_RADIUS is treated as one function-shaped
// cluster. Small enough that adjacent functions don't merge, big enough that
// a normal function-worth-of-mutations lands in the same bucket.
const LINE_CLUSTER_RADIUS = 8;

function lineBucket(line) {
  if (line == null) return "?";
  return Math.floor(line / LINE_CLUSTER_RADIUS);
}

function counter(values) {
  const c = new Map();
  for (const v of values) c.set(v, (c.get(v) || 0) + 1);
  return c;
}

function dominant(kindCounter) {
  let bestKind = "other";
  let bestCount = -1;
  for (const [k, n] of kindCounter) {
    if (n > bestCount) {
      bestCount = n;
      bestKind = k;
    }
  }
  return bestKind;
}

function linesInClusterSpan(mutants, missingLines) {
  const lines = mutants.map((m) => m.line).filter((l) => l != null);
  if (!lines.length || !missingLines?.length) return [];
  const lo = Math.min(...lines) - 2;
  const hi = Math.max(...lines) + 8;
  const inRange = missingLines.filter((ln) => ln >= lo && ln <= hi);
  return Array.from(new Set(inRange)).sort((a, b) => a - b);
}

export function planSpecs(classified, { missingLines = [] } = {}) {
  const real = classified.filter((c) => c.verdict === "real_gap");
  if (!real.length) return [];

  const clusters = new Map(); // key -> { file, function, mutants }
  for (const c of real) {
    const m = c.mutant;
    const file = m.file || "target.js";
    const bucket = lineBucket(m.line);
    const key = `${file}::${bucket}`;
    if (!clusters.has(key)) {
      clusters.set(key, { file, function: null, mutants: [] });
    }
    clusters.get(key).mutants.push(m);
  }

  const specs = [];
  for (const { file, function: fn, mutants } of clusters.values()) {
    const kc = counter(mutants.map((m) => m.kind || "other"));
    const dom = dominant(kc);
    specs.push({
      file,
      function: fn,
      dominant_kind: dom,
      technique_hint: TECHNIQUE_BY_KIND[dom] || TECHNIQUE_BY_KIND.other,
      survivors: mutants,
      uncovered_lines: linesInClusterSpan(mutants, missingLines),
    });
  }
  return specs;
}

export const _internal = { lineBucket, dominant, linesInClusterSpan };
