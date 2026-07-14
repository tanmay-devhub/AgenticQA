/**
 * LoopResult wrapper. Mirrors Python's ``LoopResult`` dataclass so the same
 * derived fields (finalReport, totalUsage) exist on the JS side without every
 * caller rebuilding them by hand.
 *
 * Kept intentionally small: this is a value object, not a controller. The
 * loop mutates ``rounds`` and ``stopped_reason`` directly; getters compute
 * derived views on demand.
 */

const EMPTY_BUCKET = () => ({ calls: 0, prompt_tokens: 0, completion_tokens: 0 });

export class LoopResult {
  constructor(workdir) {
    this.workdir = workdir;
    this.rounds = [];
    this.stopped_reason = "";
  }

  get finalReport() {
    for (let i = this.rounds.length - 1; i >= 0; i--) {
      if (this.rounds[i].report) return this.rounds[i].report;
    }
    return null;
  }

  get finalKillRate() {
    const r = this.finalReport;
    return r ? r.kill_rate : null;
  }

  get pytestOk() {
    // Last round's test-runner outcome. Named for wire-format compat with
    // the Python side even though a JS loop uses `node --test`, not pytest.
    if (!this.rounds.length) return false;
    return !!this.rounds[this.rounds.length - 1].pytest_ok;
  }

  get totalUsage() {
    const agg = {
      codegen: EMPTY_BUCKET(),
      planner: EMPTY_BUCKET(),
      analysis: EMPTY_BUCKET(),
    };
    for (const r of this.rounds) {
      for (const role of ["codegen", "planner", "analysis"]) {
        const bucket = r.usage?.[role] || EMPTY_BUCKET();
        agg[role].calls += bucket.calls;
        agg[role].prompt_tokens += bucket.prompt_tokens;
        agg[role].completion_tokens += bucket.completion_tokens;
      }
    }
    return agg;
  }

  toJSON() {
    return {
      workdir: this.workdir,
      stopped_reason: this.stopped_reason,
      rounds: this.rounds,
      total_usage: this.totalUsage,
      final_kill_rate: this.finalKillRate,
    };
  }
}
