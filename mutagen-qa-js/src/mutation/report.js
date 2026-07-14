/**
 * MutationReport shape. Matches mutagen-qa/src/mutagen/mutation/report.py so
 * the existing Python dashboard renders JS runs without change.
 *
 * MutationKind is a coarse taxonomy across languages ("constant", "comparison",
 * "boolean", "arithmetic", "return", "keyword", "call", "other").
 */

export function makeMutationReport({
  total,
  killed,
  survived,
  timeout = 0,
  suspicious = 0,
  skipped = 0,
  survivors = [],
  disabledTypes = [],
}) {
  const killable = total - timeout - skipped;
  const killRate = killable > 0 ? killed / killable : 0.0;
  return {
    total,
    killed,
    survived,
    timeout,
    suspicious,
    skipped,
    kill_rate: killRate,
    survivors,
    disabled_types: disabledTypes,
  };
}

export function formatSummary(report) {
  return (
    `killed=${report.killed}/${report.total}  ` +
    `survived=${report.survived}  ` +
    `timeout=${report.timeout}  ` +
    `suspicious=${report.suspicious}  ` +
    `kill_rate=${(report.kill_rate * 100).toFixed(1)}%`
  );
}
