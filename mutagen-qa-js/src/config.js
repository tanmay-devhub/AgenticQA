/**
 * Central config for mutagen-qa-js. Mirrors mutagen-qa/src/mutagen/config.py:
 *
 * Three LLM roles:
 *   codegen  -- writes jest tests. Default: Ollama Cloud `minimax-m3:cloud`
 *   planner  -- decides which mutant to attack next. Default: Gemini 2.5 Pro
 *   analysis -- post-run report. Default: Ollama Cloud `gpt-oss:120b-cloud`
 *
 * Env-var overrides (read at getConfig() time):
 *   MUTAGEN_{CODEGEN,PLANNER,ANALYSIS}_MODEL
 *   MUTAGEN_{CODEGEN,PLANNER,ANALYSIS}_API_BASE
 *   MUTAGEN_{CODEGEN,PLANNER,ANALYSIS}_API_KEY_ENV
 */

function applyEnvOverrides(role, prefix) {
  const m = process.env[`MUTAGEN_${prefix}_MODEL`];
  const b = process.env[`MUTAGEN_${prefix}_API_BASE`];
  const k = process.env[`MUTAGEN_${prefix}_API_KEY_ENV`];
  return {
    ...role,
    ...(m ? { model: m } : {}),
    ...(b ? { apiBase: b } : {}),
    ...(k ? { apiKeyEnv: k } : {}),
  };
}

export function getConfig() {
  return {
    llm: {
      codegen: applyEnvOverrides(
        {
          model: "ollama/minimax-m3:cloud",
          apiBase: "https://ollama.com",
          apiKeyEnv: "OLLAMA_API_KEY",
          temperature: 0.2,
          // 32k: minimax-m3's <think> phase on the JS T2 prompt saturated 16k
          // in live testing without emitting a single test. Doubling gives a
          // comfortable headroom without inviting runaway completions.
          maxTokens: 32768,
        },
        "CODEGEN",
      ),
      planner: applyEnvOverrides(
        {
          model: "gemini/gemini-2.5-pro",
          apiBase: null,
          apiKeyEnv: "GEMINI_API_KEY",
          temperature: 0.1,
          maxTokens: 2048,
        },
        "PLANNER",
      ),
      analysis: applyEnvOverrides(
        {
          model: "ollama/gpt-oss:120b-cloud",
          apiBase: "https://ollama.com",
          apiKeyEnv: "OLLAMA_API_KEY",
          temperature: 0.2,
          maxTokens: 4096,
        },
        "ANALYSIS",
      ),
    },
    sandbox: {
      backend: process.env.MUTAGEN_SANDBOX_BACKEND || "subprocess",
      nodeTimeoutS: 30,
      strykerTimeoutS: 180,
    },
    mutation: {
      // Stryker mutator names to skip. Strings are usually error-message wording;
      // our generator (correctly) refuses to assert exact messages.
      disabledMutators: ["StringLiteral"],
    },
    loop: {
      maxRounds: 3,
      plateauDelta: 0.02,
      wallClockS: 600,
    },
  };
}
