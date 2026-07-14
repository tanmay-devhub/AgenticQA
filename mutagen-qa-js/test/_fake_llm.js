/**
 * Deterministic scriptable LLM for tests.
 *
 * Each call pops the next scripted response off `queue`. Assign
 * `nextResponse` to prime a single response, or push whole {system,
 * user, response} triples for stricter assertions.
 */

export class FakeLLM {
  constructor({ queue = [] } = {}) {
    this._queue = [...queue];
    this.calls = [];
    this.usage = {
      codegen: { calls: 0, promptTokens: 0, completionTokens: 0 },
      planner: { calls: 0, promptTokens: 0, completionTokens: 0 },
      analysis: { calls: 0, promptTokens: 0, completionTokens: 0 },
    };
  }

  queueResponse(text, opts = {}) {
    this._queue.push({ text, ...opts });
  }

  snapshotUsage() {
    return JSON.parse(JSON.stringify(this.usage));
  }

  usageDelta(previous) {
    const out = {};
    for (const r of ["codegen", "planner", "analysis"]) {
      out[r] = {
        calls: this.usage[r].calls - previous[r].calls,
        promptTokens: this.usage[r].promptTokens - previous[r].promptTokens,
        completionTokens: this.usage[r].completionTokens - previous[r].completionTokens,
      };
    }
    return out;
  }

  async complete(role, { system, user }) {
    this.calls.push({ role, system, user });
    const entry = this._queue.shift();
    if (!entry) throw new Error(`FakeLLM: unexpected ${role} call (queue empty)`);
    if (entry.throw) throw entry.throw;
    this.usage[role].calls += 1;
    this.usage[role].promptTokens += entry.promptTokens ?? 100;
    this.usage[role].completionTokens += entry.completionTokens ?? 50;
    return {
      text: entry.text ?? "",
      promptTokens: entry.promptTokens ?? 100,
      completionTokens: entry.completionTokens ?? 50,
      model: "fake",
      finishReason: entry.finishReason ?? "stop",
    };
  }
}
