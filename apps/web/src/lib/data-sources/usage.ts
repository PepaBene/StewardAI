/**
 * Usage over time, from `/sources/usage` (typed): credits and requests per quota window.
 * Windows are bucketed by their start day; a day with no ledger row is a gap (null), not zero.
 */
import { num } from "@/lib/parse-untyped";

export type UsageWindow = {
  provider: string;
  endpoint: string;
  windowKind: string;
  windowStart: string;
  credits: number | null;
  requests: number | null;
};

export function parseUsage(payload: unknown): UsageWindow[] {
  if (!Array.isArray(payload)) return [];
  return payload
    .filter((r): r is Record<string, unknown> => typeof r === "object" && r !== null)
    .map((r) => ({
      provider: typeof r.provider === "string" ? r.provider : "unknown",
      endpoint: typeof r.endpoint === "string" ? r.endpoint : "unknown",
      windowKind: typeof r.window_kind === "string" ? r.window_kind : "unknown",
      windowStart: typeof r.window_start === "string" ? r.window_start : "",
      credits: num(r.credits),
      requests: num(r.requests),
    }))
    .filter((r) => r.windowStart.length >= 10);
}

export function providersOf(rows: readonly UsageWindow[]): string[] {
  return [...new Set(rows.map((r) => r.provider))].sort();
}

/** Daily total per provider (or all providers when null), oldest first; only days with a ledger row appear. */
export function dailySeries(rows: readonly UsageWindow[], provider: string | null, metric: "credits" | "requests") {
  const byDay = new Map<string, number>();
  for (const r of rows) {
    if (provider && r.provider !== provider) continue;
    const v = r[metric];
    if (v === null) continue;
    const day = r.windowStart.slice(0, 10);
    byDay.set(day, (byDay.get(day) ?? 0) + v);
  }
  return [...byDay.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, value]) => ({ date, value }));
}

/** Total per endpoint for the loaded rows, largest first. */
export function endpointTotals(rows: readonly UsageWindow[], provider: string | null, metric: "credits" | "requests") {
  const m = new Map<string, number>();
  for (const r of rows) {
    if (provider && r.provider !== provider) continue;
    const v = r[metric];
    if (v === null) continue;
    const key = `${r.provider} ${r.endpoint}`;
    m.set(key, (m.get(key) ?? 0) + v);
  }
  return [...m.entries()].map(([key, total]) => ({ key, total })).sort((a, b) => b.total - a.total);
}
