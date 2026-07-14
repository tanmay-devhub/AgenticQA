/**
 * Provider-dispatching LLM client. Three roles from config; each role picks
 * its provider from the model-string prefix ("ollama/", "gemini/", ...).
 *
 * Retries transient failures (HTTP 429 / 5xx / network) up to MAX_ATTEMPTS
 * with exponential backoff, matching the Python client's behavior.
 */

import { ollamaComplete } from "./providers/ollama.js";
import { geminiComplete } from "./providers/gemini.js";

const MAX_ATTEMPTS = 3;
const INITIAL_BACKOFF_MS = 1000;

function pickProvider(model) {
  if (model.startsWith("ollama/")) return "ollama";
  if (model.startsWith("gemini/")) return "gemini";
  // Assume anything else is OpenAI-compatible via env override.
  return "ollama";
}

function isTransient(err) {
  if (err?.transient) return true;
  const msg = String(err?.message || "").toLowerCase();
  return msg.includes("rate limit") || msg.includes("timeout") || msg.includes("connection") || msg.includes("econnreset");
}

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

export class LLM {
  constructor(config, { sleep: sleepFn = sleep } = {}) {
    this._config = config;
    this._sleep = sleepFn;
    this.usage = {
      codegen: { calls: 0, promptTokens: 0, completionTokens: 0 },
      planner: { calls: 0, promptTokens: 0, completionTokens: 0 },
      analysis: { calls: 0, promptTokens: 0, completionTokens: 0 },
    };
  }

  snapshotUsage() {
    return JSON.parse(JSON.stringify(this.usage));
  }

  usageDelta(previous) {
    const roles = ["codegen", "planner", "analysis"];
    const out = {};
    for (const r of roles) {
      out[r] = {
        calls: this.usage[r].calls - previous[r].calls,
        promptTokens: this.usage[r].promptTokens - previous[r].promptTokens,
        completionTokens: this.usage[r].completionTokens - previous[r].completionTokens,
      };
    }
    return out;
  }

  async complete(role, { system, user }) {
    const roleCfg = this._config.llm[role];
    if (!roleCfg) throw new Error(`unknown LLM role: ${role}`);
    const apiKey = roleCfg.apiKeyEnv ? process.env[roleCfg.apiKeyEnv] : null;
    if (roleCfg.apiKeyEnv && !apiKey) {
      throw new Error(`role ${role} needs env var ${roleCfg.apiKeyEnv} but it is not set`);
    }

    const messages = [
      { role: "system", content: system },
      { role: "user", content: user },
    ];
    const provider = pickProvider(roleCfg.model);

    let lastErr;
    for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
      try {
        let resp;
        if (provider === "gemini") {
          resp = await geminiComplete({
            model: roleCfg.model,
            apiKey,
            messages,
            temperature: roleCfg.temperature,
            maxTokens: roleCfg.maxTokens,
          });
        } else {
          resp = await ollamaComplete({
            apiBase: roleCfg.apiBase,
            apiKey,
            model: roleCfg.model,
            messages,
            temperature: roleCfg.temperature,
            maxTokens: roleCfg.maxTokens,
          });
        }
        this.usage[role].calls += 1;
        this.usage[role].promptTokens += resp.promptTokens ?? 0;
        this.usage[role].completionTokens += resp.completionTokens ?? 0;
        return resp; // includes finishReason for callers that care
      } catch (err) {
        lastErr = err;
        if (attempt === MAX_ATTEMPTS - 1 || !isTransient(err)) throw err;
        await this._sleep(INITIAL_BACKOFF_MS * 2 ** attempt);
      }
    }
    throw lastErr;
  }
}
