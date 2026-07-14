/**
 * Google Gemini provider (native REST API).
 *
 * Config model strings look like "gemini/gemini-2.5-pro"; we strip the
 * "gemini/" prefix. System + user turns are collapsed into Gemini's
 * `systemInstruction` and `contents` shape.
 */

const GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta";

export async function geminiComplete({ model, apiKey, messages, temperature, maxTokens }) {
  const bareModel = model.startsWith("gemini/") ? model.slice("gemini/".length) : model;
  const url = `${GEMINI_BASE}/models/${bareModel}:generateContent?key=${encodeURIComponent(apiKey)}`;

  const systemMsg = messages.find((m) => m.role === "system");
  const userMsgs = messages.filter((m) => m.role !== "system");

  const body = {
    contents: userMsgs.map((m) => ({
      role: m.role === "assistant" ? "model" : "user",
      parts: [{ text: m.content }],
    })),
    generationConfig: {
      temperature,
      maxOutputTokens: maxTokens,
    },
  };
  if (systemMsg) {
    body.systemInstruction = { parts: [{ text: systemMsg.content }] };
  }

  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    const err = new Error(`gemini HTTP ${res.status}: ${text.slice(0, 500)}`);
    err.status = res.status;
    err.transient = res.status === 429 || res.status >= 500;
    throw err;
  }
  const data = await res.json();
  const candidate = data.candidates?.[0] ?? {};
  const parts = candidate.content?.parts ?? [];
  const text = parts.map((p) => p.text ?? "").join("");
  const usage = data.usageMetadata ?? {};
  return {
    text,
    promptTokens: usage.promptTokenCount ?? null,
    completionTokens: usage.candidatesTokenCount ?? null,
    model: bareModel,
    // MAX_TOKENS = we hit the cap mid-completion. Callers that care about
    // whether the response is complete (codegen) should inspect this so a
    // truncated function body isn't sent to the test runner.
    finishReason: candidate.finishReason ?? null,
  };
}
