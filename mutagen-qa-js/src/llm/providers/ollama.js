/**
 * Ollama Cloud provider. Speaks OpenAI-compatible /v1/chat/completions.
 *
 * The model string arrives from config as "ollama/<name>"; the OpenAI-compat
 * endpoint doesn't want the prefix. We strip it here and let the caller pass
 * the raw model.
 */

const OLLAMA_CHAT_PATH = "/v1/chat/completions";

export async function ollamaComplete({ apiBase, apiKey, model, messages, temperature, maxTokens }) {
  const url = (apiBase || "https://ollama.com").replace(/\/$/, "") + OLLAMA_CHAT_PATH;
  const bareModel = model.startsWith("ollama/") ? model.slice("ollama/".length) : model;
  const body = {
    model: bareModel,
    messages,
    temperature,
    max_tokens: maxTokens,
  };
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    const err = new Error(`ollama HTTP ${res.status}: ${text.slice(0, 500)}`);
    err.status = res.status;
    err.transient = res.status === 429 || res.status >= 500;
    throw err;
  }
  const data = await res.json();
  const choice = data.choices?.[0];
  const usage = data.usage ?? {};
  // OpenAI-compatible endpoints emit finish_reason = "length" when the max
  // completion budget was hit; anything else means the model chose to stop.
  return {
    text: choice?.message?.content ?? "",
    promptTokens: usage.prompt_tokens ?? null,
    completionTokens: usage.completion_tokens ?? null,
    model: bareModel,
    finishReason: choice?.finish_reason ?? null,
  };
}
