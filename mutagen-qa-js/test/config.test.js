import { test } from "node:test";
import assert from "node:assert/strict";

import { getConfig } from "../src/config.js";

test("getConfig defaults: codegen is Ollama Cloud minimax", () => {
  const cfg = getConfig();
  assert.equal(cfg.llm.codegen.model, "ollama/minimax-m3:cloud");
  assert.equal(cfg.llm.codegen.apiBase, "https://ollama.com");
  assert.equal(cfg.llm.codegen.apiKeyEnv, "OLLAMA_API_KEY");
  // 32k on the JS side -- minimax's <think> saturated 16k on the T2 prompt.
  assert.equal(cfg.llm.codegen.maxTokens, 32768);
});

test("getConfig defaults: planner is Gemini 2.5 Pro", () => {
  const cfg = getConfig();
  assert.equal(cfg.llm.planner.model, "gemini/gemini-2.5-pro");
  assert.equal(cfg.llm.planner.apiKeyEnv, "GEMINI_API_KEY");
});

test("MUTAGEN_CODEGEN_MODEL env override wins", () => {
  const prev = process.env.MUTAGEN_CODEGEN_MODEL;
  process.env.MUTAGEN_CODEGEN_MODEL = "ollama/qwen3-coder:30b";
  try {
    const cfg = getConfig();
    assert.equal(cfg.llm.codegen.model, "ollama/qwen3-coder:30b");
  } finally {
    if (prev === undefined) delete process.env.MUTAGEN_CODEGEN_MODEL;
    else process.env.MUTAGEN_CODEGEN_MODEL = prev;
  }
});

test("mutation defaults exclude StringLiteral to match Python's string/fstring filter", () => {
  const cfg = getConfig();
  assert.ok(cfg.mutation.disabledMutators.includes("StringLiteral"));
});
