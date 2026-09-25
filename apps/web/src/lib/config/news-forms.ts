import type {
  EventRulesConfig,
  EventType,
  NewsSource,
  NewsSourcesConfig,
  NotificationRule,
  NotificationsConfig,
} from "@/lib/api/client";
import { EVENT_TYPES } from "@/lib/news/labels";

import { lines } from "./theses-form";

/*
 * Form state for the Phase 3 config domains. Numbers the owner types stay strings until
 * saved, so a half-typed value is never coerced; the backend's 422 answer reports it.
 * Lists of free text (tickers, domains) are one entry per line.
 */

let counter = 0;
const nextKey = () => `n${++counter}`;

function numberOrText(text: string): number | string {
  const trimmed = text.trim();
  const n = Number(trimmed);
  return trimmed !== "" && Number.isFinite(n) ? n : trimmed;
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

export type Channel = NotificationRule["channels"][number];
export const CHANNELS: readonly Channel[] = ["telegram", "email"];
export const CHANNEL_LABELS: Record<Channel, string> = { telegram: "Telegram", email: "Email" };
export type QuietMode = NonNullable<NotificationRule["during_quiet_hours"]>;
export const QUIET_MODES: readonly { value: QuietMode; label: string }[] = [
  { value: "hold_until_end", label: "Hold until quiet hours end" },
  { value: "send", label: "Send anyway" },
  { value: "digest_only", label: "Digest only" },
];

export type RuleForm = {
  key: string;
  id: string;
  enabled: boolean;
  severities: (1 | 2 | 3)[];
  event_types: EventType[];
  tickers: string;
  channels: Channel[];
  during_quiet_hours: QuietMode;
};

export type NotificationsForm = {
  rules: RuleForm[];
  quiet_enabled: boolean;
  quiet_start: string;
  quiet_end: string;
  quiet_timezone: string;
  daily_alert_cap: string;
  muted_tickers: string;
  muted_event_types: EventType[];
  repeat_throttle_hours: string;
  digest_channels: Channel[];
};

export function newRule(): RuleForm {
  return {
    key: nextKey(),
    id: "",
    enabled: true,
    severities: [3],
    event_types: [],
    tickers: "",
    channels: ["telegram"],
    during_quiet_hours: "hold_until_end",
  };
}

/** The backend's defaults, for when nothing is saved yet. */
export const DEFAULT_NOTIFICATIONS: NotificationsConfig = {
  schema_version: 1,
  rules: [
    {
      id: "instant-severity-3",
      enabled: true,
      match: { severities: [3], event_types: [], tickers: [] },
      channels: ["telegram"],
      during_quiet_hours: "hold_until_end",
    },
  ],
  quiet_hours: { enabled: true, start: "22:00", end: "07:00", timezone: "Europe/Prague" },
  daily_alert_cap: 5,
  muted_tickers: [],
  muted_event_types: [],
  repeat_throttle_hours: 24,
  digest_channels: ["email"],
};

export function notificationsFromBody(body: NotificationsConfig | null): NotificationsForm {
  const b = body ?? DEFAULT_NOTIFICATIONS;
  const q = b.quiet_hours ?? DEFAULT_NOTIFICATIONS.quiet_hours!;
  return {
    rules: (b.rules ?? []).map((r) => ({
      key: nextKey(),
      id: r.id,
      enabled: r.enabled ?? true,
      severities: [...r.match.severities],
      event_types: [...(r.match.event_types ?? [])],
      tickers: (r.match.tickers ?? []).join("\n"),
      channels: [...r.channels],
      during_quiet_hours: r.during_quiet_hours ?? "hold_until_end",
    })),
    quiet_enabled: q.enabled ?? true,
    quiet_start: q.start ?? "22:00",
    quiet_end: q.end ?? "07:00",
    quiet_timezone: q.timezone ?? "Europe/Prague",
    daily_alert_cap: b.daily_alert_cap === null ? "" : String(b.daily_alert_cap),
    muted_tickers: (b.muted_tickers ?? []).join("\n"),
    muted_event_types: [...(b.muted_event_types ?? [])],
    repeat_throttle_hours: String(b.repeat_throttle_hours),
    digest_channels: [...(b.digest_channels ?? [])],
  };
}

export function notificationsToBody(form: NotificationsForm): NotificationsConfig {
  const byOrder = <T>(all: readonly T[], picked: readonly T[]) => all.filter((x) => picked.includes(x));
  return {
    schema_version: 1,
    rules: form.rules.map((r) => ({
      id: r.id.trim(),
      enabled: r.enabled,
      match: {
        severities: [...r.severities].sort(),
        event_types: byOrder(EVENT_TYPES, r.event_types),
        tickers: lines(r.tickers),
      },
      channels: byOrder(CHANNELS, r.channels),
      during_quiet_hours: r.during_quiet_hours,
    })),
    quiet_hours: {
      enabled: form.quiet_enabled,
      start: form.quiet_start.trim(),
      end: form.quiet_end.trim(),
      timezone: form.quiet_timezone.trim(),
    },
    daily_alert_cap:
      form.daily_alert_cap.trim() === "" ? null : (numberOrText(form.daily_alert_cap) as number),
    muted_tickers: lines(form.muted_tickers),
    muted_event_types: byOrder(EVENT_TYPES, form.muted_event_types),
    repeat_throttle_hours: numberOrText(form.repeat_throttle_hours) as number,
    digest_channels: byOrder(CHANNELS, form.digest_channels),
  };
}

// ---------------------------------------------------------------------------
// News sources
// ---------------------------------------------------------------------------

export type SourceType = NewsSource["type"];
export const SOURCE_TYPES: readonly { value: SourceType; label: string }[] = [
  { value: "rss", label: "RSS / Atom feed" },
  { value: "page", label: "News page (no feed)" },
  { value: "edgar", label: "SEC EDGAR filings (per holding)" },
  { value: "finnhub", label: "Finnhub company news (per holding)" },
  { value: "google_news", label: "Google News query (per holding)" },
];
export const PER_HOLDING: readonly SourceType[] = ["edgar", "finnhub", "google_news"];

export type SourceForm = {
  key: string;
  id: string;
  name: string;
  tier: 1 | 2 | 3;
  type: SourceType;
  url: string;
  enabled: boolean;
  tickers: string;
};

export type SourcesForm = {
  sources: SourceForm[];
  domain_tiers: string;
  blocked_domains: string;
  unknown_outlet_tier: 1 | 2 | 3;
  freshness_hours: string;
  include_tier3: boolean;
};

export function newSource(): SourceForm {
  return { key: nextKey(), id: "", name: "", tier: 2, type: "rss", url: "", enabled: true, tickers: "" };
}

/** "reuters.com 2" per line <-> [{domain, tier}]. */
export function parseDomainTiers(text: string): { domain: string; tier: number | string }[] {
  return lines(text).map((line) => {
    const [domain, tier = ""] = line.split(/\s+/);
    return { domain: domain.toLowerCase(), tier: numberOrText(tier) };
  });
}

export function sourcesFromBody(body: NewsSourcesConfig | null, defaults: NewsSourcesConfig): SourcesForm {
  const b = body ?? defaults;
  return {
    sources: (b.sources ?? []).map((s) => ({
      key: nextKey(),
      id: s.id,
      name: s.name,
      tier: s.tier,
      type: s.type,
      url: s.url ?? "",
      enabled: s.enabled ?? true,
      tickers: (s.tickers ?? []).join("\n"),
    })),
    domain_tiers: (b.domain_tiers ?? []).map((d) => `${d.domain} ${d.tier}`).join("\n"),
    blocked_domains: (b.blocked_domains ?? []).join("\n"),
    unknown_outlet_tier: b.unknown_outlet_tier ?? 3,
    freshness_hours: String(b.freshness_hours ?? 72),
    include_tier3: b.include_tier3 ?? false,
  };
}

export function sourcesToBody(form: SourcesForm): NewsSourcesConfig {
  return {
    schema_version: 1,
    sources: form.sources.map((s) => ({
      id: s.id.trim(),
      name: s.name.trim(),
      tier: s.tier,
      type: s.type,
      url: PER_HOLDING.includes(s.type) ? null : s.url.trim() || null,
      enabled: s.enabled,
      tickers: lines(s.tickers),
    })),
    domain_tiers: parseDomainTiers(form.domain_tiers) as NewsSourcesConfig["domain_tiers"],
    blocked_domains: lines(form.blocked_domains).map((d) => d.toLowerCase()),
    unknown_outlet_tier: form.unknown_outlet_tier,
    freshness_hours: numberOrText(form.freshness_hours) as number,
    include_tier3: form.include_tier3,
  };
}

// ---------------------------------------------------------------------------
// Event rules
// ---------------------------------------------------------------------------

export const DEFAULT_SEVERITIES: Record<EventType, number> = {
  guidance_change: 3,
  m_and_a: 3,
  dividend_cut: 3,
  earnings_release: 2,
  management_change: 2,
  regulatory_legal: 2,
  capital_allocation: 2,
  credit_rating: 2,
  central_bank: 2,
  macro_surprise: 2,
  insider_transaction: 1,
  product_business: 1,
  sector_competitor: 1,
  index_change: 1,
  analyst_rating: 0,
  delisting: 2,
  entity_status: 1,
  other: 0,
};

export type OverrideForm = { key: string; ticker: string; event_type: EventType; severity: number };

export type EventRulesForm = {
  severities: Record<EventType, number>;
  overrides: OverrideForm[];
  escalate_when_thesis_linked: EventType[];
  earnings_check_on: EventType[];
  thesis_breach_severity: number;
  insider_cluster_count: string;
  insider_cluster_days: string;
  recycled_after_days: string;
};

export function newOverride(): OverrideForm {
  return { key: nextKey(), ticker: "", event_type: "product_business", severity: 2 };
}

export function eventRulesFromBody(body: EventRulesConfig | null): EventRulesForm {
  return {
    severities: { ...DEFAULT_SEVERITIES, ...(body?.severities as Partial<Record<EventType, number>> | undefined) },
    overrides: (body?.overrides ?? []).map((o) => ({ key: nextKey(), ...o })),
    escalate_when_thesis_linked: [...(body?.escalate_when_thesis_linked ?? ["regulatory_legal"])],
    earnings_check_on: [...(body?.earnings_check_on ?? ["earnings_release"])],
    thesis_breach_severity: body?.thesis_breach_severity ?? 3,
    insider_cluster_count: String(body?.insider_cluster_count ?? 3),
    insider_cluster_days: String(body?.insider_cluster_days ?? 14),
    recycled_after_days: String(body?.recycled_after_days ?? 7),
  };
}

export function eventRulesToBody(form: EventRulesForm): EventRulesConfig {
  const inOrder = (picked: EventType[]) => EVENT_TYPES.filter((t) => picked.includes(t));
  return {
    schema_version: 1,
    severities: Object.fromEntries(EVENT_TYPES.map((t) => [t, form.severities[t]])),
    overrides: form.overrides.map((o) => ({ ticker: o.ticker.trim(), event_type: o.event_type, severity: o.severity })),
    escalate_when_thesis_linked: inOrder(form.escalate_when_thesis_linked),
    earnings_check_on: inOrder(form.earnings_check_on),
    thesis_breach_severity: form.thesis_breach_severity,
    insider_cluster_count: numberOrText(form.insider_cluster_count) as number,
    insider_cluster_days: numberOrText(form.insider_cluster_days) as number,
    recycled_after_days: numberOrText(form.recycled_after_days) as number,
  };
}
