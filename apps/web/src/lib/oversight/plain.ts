/*
 * Plain words for provider and API failures. The raw HTTP status and the provider's own
 * message stay available (the callers put them under "Details"); the sentence says what
 * happened and what to do about it.
 */

export type PlainProblem = {
  /** What happened, in a sentence. */
  sentence: string;
  /** What to do about it. */
  fix: string;
  /** The raw status and message, for the collapsed Details. */
  raw: string;
};

export function plainProviderProblem(input: {
  statusCode?: number | null;
  state?: string | null;
  detail?: string | null;
}): PlainProblem {
  const code = input.statusCode ?? null;
  const state = (input.state ?? "").toLowerCase();
  const rawParts = [code !== null ? `HTTP ${code}` : null, state ? state.replaceAll("_", " ") : null, input.detail?.trim() || null];
  const raw = rawParts.filter(Boolean).join(" · ") || "No details recorded.";
  if (code === 401 || code === 403 || state.includes("denied") || state.includes("forbidden")) {
    return {
      sentence: "The provider refused our key for this data.",
      fix: "Check the key or the plan. Paid-only data stays off until that changes.",
      raw,
    };
  }
  if (code === 402 || state.includes("paid") || state.includes("plan")) {
    return {
      sentence: "This data needs a paid plan, so it is not used.",
      fix: "Nothing to do unless you upgrade the plan.",
      raw,
    };
  }
  if (code === 404) {
    return { sentence: "The provider has no data under this name.", fix: "Check the symbol or the mapping for this source.", raw };
  }
  if (code === 429 || state.includes("rate") || state.includes("quota")) {
    return { sentence: "We used up the free allowance for now.", fix: "It retries after the allowance resets. No action needed.", raw };
  }
  if (code !== null && code >= 500) {
    return { sentence: "The provider had a problem on its side.", fix: "It retries on the next run. Check again later.", raw };
  }
  if (state.includes("timeout") || state.includes("unreachable") || state.includes("network")) {
    return { sentence: "We could not reach the provider.", fix: "It retries on the next run. Check your connection if it keeps failing.", raw };
  }
  return { sentence: "The provider did not return usable data.", fix: "Open Details for the provider's message.", raw };
}

/** First letter up, trailing full stop dropped (the caller adds its own). */
export function sentenceCase(s: string): string {
  const t = s.trim().replace(/\.+$/, "");
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}

/** A provider or config key in words: "alpha_vantage/account_free" → "Alpha vantage, account free". */
export function subjectWords(subject: string): string {
  if (!/[_/]/.test(subject) || /\s/.test(subject)) return subject;
  return sentenceCase(subject.split("/").map((p) => p.replaceAll("_", " ")).join(", "));
}

/** "Just now", "3 days ago": a short age for a past timestamp. */
export function ageWords(iso: string | null | undefined, now: Date): string {
  if (!iso) return "never";
  const ms = now.getTime() - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 0) return "just now";
  const min = Math.floor(ms / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24);
  return d === 1 ? "yesterday" : `${d} days ago`;
}
