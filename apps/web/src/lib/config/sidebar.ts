import type { ConfigExport } from "@/lib/api/client";

import { domainFromName } from "./domains";

/*
 * The config sidebar: every config page, in the design's order, and a short count per domain
 * read from the one-request export of all active versions (GET /config/export).
 */

export type ConfigNavKey =
  | "strategy"
  | "theses"
  | "holding-profiles"
  | "event-rules"
  | "news-sources"
  | "notifications"
  | "research-profiles"
  | "jobs"
  | "budget"
  | "prompts"
  | "verification"
  | "checks"
  | "calendar"
  | "autonomy"
  | "history";

export type ConfigNavItem = { key: ConfigNavKey; href: string; label: string };

export const CONFIG_NAV: readonly ConfigNavItem[] = [
  { key: "strategy", href: "/config/strategy", label: "Strategy" },
  { key: "theses", href: "/config/theses", label: "Theses" },
  { key: "holding-profiles", href: "/config/holding-profiles", label: "Holding profiles" },
  { key: "event-rules", href: "/config/event-rules", label: "Event rules" },
  { key: "news-sources", href: "/config/news-sources", label: "News sources" },
  { key: "notifications", href: "/config/notifications", label: "Notifications" },
  { key: "research-profiles", href: "/config/research-profiles", label: "Research profiles" },
  { key: "jobs", href: "/config/jobs", label: "Jobs" },
  { key: "budget", href: "/config/budget", label: "Budget" },
  { key: "prompts", href: "/config/prompts", label: "Prompts" },
  { key: "verification", href: "/config/verification", label: "Verification" },
  { key: "checks", href: "/config/checks", label: "Checks" },
  { key: "calendar", href: "/config/calendar", label: "Calendar" },
  { key: "autonomy", href: "/config/autonomy", label: "Autonomy" },
  { key: "history", href: "/config/history", label: "History & export" },
];

/** The nav item a pathname belongs to (/config/history/strategy -> history), or null. */
export function activeNavKey(pathname: string): ConfigNavKey | null {
  const hit = CONFIG_NAV.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`));
  return hit?.key ?? null;
}

export type NavCount = { text: string; title: string };

const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function countFor(key: ConfigNavKey, body: Record<string, unknown>): NavCount | null {
  switch (key) {
    case "strategy": {
      const n = list(body.target_positions).length;
      return { text: String(n), title: plural(n, "position target") };
    }
    case "theses": {
      const n = list(body.theses).length;
      return { text: String(n), title: plural(n, "thesis", "theses") };
    }
    case "holding-profiles":
    case "research-profiles": {
      const n = list(body.profiles).length;
      return { text: String(n), title: plural(n, "profile") };
    }
    case "event-rules": {
      const n = Object.keys(obj(body.severities)).length;
      return { text: String(n), title: `${plural(n, "event type")} with a severity` };
    }
    case "news-sources": {
      const sources = list(body.sources).map(obj);
      const on = sources.filter((s) => s.enabled !== false).length;
      return { text: String(on), title: `${on} of ${plural(sources.length, "source")} enabled` };
    }
    case "notifications": {
      const on = list(body.rules).map(obj).filter((r) => r.enabled !== false).length;
      return { text: String(on), title: `${plural(on, "rule")} enabled` };
    }
    case "jobs": {
      const scheduled = Object.values(body)
        .map(obj)
        .filter((j) => typeof j.cron === "string" && j.enabled !== false).length;
      return { text: String(scheduled), title: `${plural(scheduled, "scheduled job")} enabled` };
    }
    case "budget": {
      const cap = body.monthly_cap_usd;
      if (typeof cap !== "number" || !Number.isFinite(cap)) return { text: "no cap", title: "No monthly AI spend cap" };
      return { text: `$${Math.round(cap)}`, title: `Monthly AI spend cap: $${cap}` };
    }
    case "prompts": {
      const n = Object.keys(obj(body.overrides)).length;
      return n === 0 ? { text: "built-in", title: "Every prompt uses its built-in text" } : { text: String(n), title: plural(n, "prompt override") };
    }
    default:
      return null;
  }
}

/** Counts per nav item from the export's active bodies; items with nothing cheap to count are absent. */
export function navCounts(exported: Pick<ConfigExport, "domains"> | null | undefined): Partial<Record<ConfigNavKey, NavCount>> {
  const out: Partial<Record<ConfigNavKey, NavCount>> = {};
  for (const [name, domain] of Object.entries(exported?.domains ?? {})) {
    const slug = domainFromName(name);
    if (!slug) continue;
    const count = countFor(slug as ConfigNavKey, obj(obj(domain).body));
    if (count) out[slug as ConfigNavKey] = count;
  }
  return out;
}
