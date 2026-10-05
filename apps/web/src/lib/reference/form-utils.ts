/** Shared helpers for owner-entry forms: error text from API responses and field parsing. */

export type FieldErrors<K extends string = string> = Partial<Record<K, string>>;

/** A readable message from a FastAPI error body (string detail, or a list of validation items). */
export function apiErrorText(error: unknown, status: number): string {
  const detail = typeof error === "object" && error !== null && "detail" in error ? (error as { detail: unknown }).detail : null;
  if (typeof detail === "string" && detail) return `Not saved (HTTP ${status}): ${detail}`;
  if (Array.isArray(detail) && detail.length > 0) {
    const parts = detail.map((d) => {
      if (typeof d !== "object" || d === null) return String(d);
      const item = d as { msg?: unknown; loc?: unknown };
      const where = Array.isArray(item.loc) ? item.loc.filter((p) => p !== "body").join(".") : "";
      return `${where ? `${where}: ` : ""}${typeof item.msg === "string" ? item.msg : "invalid"}`;
    });
    return `Not saved (HTTP ${status}): ${parts.join("; ")}`;
  }
  return `Not saved (HTTP ${status}).`;
}

/** An optional decimal: blank is null (not entered), anything else must be a finite number. */
export function parseOptionalNumber(text: string): number | null | "invalid" {
  const t = text.trim().replace(",", ".");
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : "invalid";
}

export const trimOrNull = (text: string): string | null => {
  const t = text.trim();
  return t === "" ? null : t;
};

export const URL_RE = /^https?:\/\/\S+$/i;

/** A datetime-local value (no zone) read as UTC, as the exchange-session API stores UTC. */
export function utcFromLocalInput(text: string): string | null {
  const t = text.trim();
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(t)) return null;
  return `${t.length === 16 ? `${t}:00` : t}Z`;
}
