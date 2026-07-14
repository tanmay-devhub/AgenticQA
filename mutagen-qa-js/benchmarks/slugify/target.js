// Turn arbitrary text into a URL-safe slug.

/**
 * Rules:
 *   - lowercase, hyphen-separated ASCII.
 *   - unicode is folded to ASCII (café -> cafe).
 *   - runs of non-alphanumeric characters collapse to a single hyphen.
 *   - leading/trailing hyphens are stripped.
 *   - the result is truncated to maxLen with no dangling hyphen.
 *   - non-string input -> TypeError; maxLen < 1 -> RangeError.
 *   - empty / all-punctuation input -> "".
 */
export function slugify(text, maxLen = 60) {
  if (typeof text !== "string") {
    throw new TypeError("text must be a string");
  }
  if (maxLen < 1) {
    throw new RangeError("maxLen must be positive");
  }

  const normalized = text.normalize("NFKD");
  // Strip combining marks after NFKD decomposition -- this is JS's version
  // of Python's ascii encode+ignore trick for accent folding.
  const asciiText = normalized.replace(/[̀-ͯ]/g, "");
  const lowered = asciiText.toLowerCase();

  const out = [];
  let prevHyphen = false;
  for (const ch of lowered) {
    if (/[a-z0-9]/.test(ch)) {
      out.push(ch);
      prevHyphen = false;
    } else if (!prevHyphen) {
      out.push("-");
      prevHyphen = true;
    }
  }
  let slug = out.join("").replace(/^-+|-+$/g, "");

  if (slug.length > maxLen) {
    slug = slug.slice(0, maxLen).replace(/-+$/g, "");
  }
  return slug;
}
