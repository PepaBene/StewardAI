import { normaliseSeparators } from "@/lib/number-input";
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

export type QuietDay = NonNullable<NotificationsConfig["quiet_all_day_on"]>[number];
export const QUIET_DAYS: readonly { value: QuietDay; label: string }[] = [
  { value: "weekends", label: "Weekends" },
  { value: "holidays", label: "Public holidays" },
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
  quiet_all_day_on: QuietDay[];
  holiday_country: string;
  system_alert_severity: 0 | 1 | 2 | 3;
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
  daily_alert_cap: null,
  muted_tickers: [],
  muted_event_types: [],
  repeat_throttle_hours: 24,
  quiet_all_day_on: [],
  holiday_country: "CZ",
  system_alert_severity: 3,
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
    quiet_all_day_on: [...(b.quiet_all_day_on ?? [])],
    holiday_country: b.holiday_country ?? "CZ",
    system_alert_severity: (b.system_alert_severity ?? 3) as 0 | 1 | 2 | 3,
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
    quiet_all_day_on: byOrder(
      QUIET_DAYS.map((d) => d.value),
      form.quiet_all_day_on,
    ),
    holiday_country: form.holiday_country.trim().toUpperCase(),
    system_alert_severity: form.system_alert_severity,
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
  { value: "rns", label: "RNS, UK regulatory news (per holding)" },
  { value: "eqs", label: "EQS News, German regulatory news (per holding)" },
  { value: "edgar_fts", label: "SEC EDGAR full-text search (per holding)" },
  { value: "alpha_vantage", label: "Alpha Vantage news (per holding)" },
  { value: "news_sitemap", label: "News sitemap (Google News sitemap)" },
  { value: "gdelt", label: "GDELT DOC search (per holding)" },
  { value: "ai_search", label: "AI web search leads (per holding, own job)" },
  { value: "tdnet", label: "TDnet, Tokyo disclosures (per holding)" },
  { value: "hkex", label: "HKEXnews, Hong Kong disclosures (per holding)" },
  { value: "cninfo", label: "CNINFO, Shanghai and Shenzhen disclosures (per holding)" },
  { value: "dart", label: "DART, Korean disclosures (per holding, needs DART_API_KEY)" },
  { value: "gazette", label: "The Gazette, UK notices (per holding)" },
  { value: "filings", label: "EDINET and Companies House filings already stored (per holding)" },
];
/** Types whose URL is built per holding (apps/backend/src/steward/config/news_sources.py). */
export const PER_HOLDING: readonly SourceType[] = ["edgar", "finnhub", "google_news", "rns", "eqs", "edgar_fts", "alpha_vantage", "gdelt", "ai_search", "tdnet", "hkex", "cninfo", "dart", "gazette", "filings"];

/** Where a per-holding source finds each holding, shown instead of a URL field. */
export const PER_HOLDING_NOTES: Partial<Record<SourceType, string>> = {
  edgar: "Every holding's SEC filings, from its CIK.",
  finnhub: "US company news for each holding's symbol.",
  google_news: "A Google News query per holding from its names (unofficial endpoint).",
  rns: "UK regulatory announcements per holding (Investegate company page); applies to holdings whose home market is the UK.",
  eqs: "German regulatory announcements from the holding profile's regulatory news URLs; applies to holdings whose home market is Germany.",
  edgar_fts: "SEC full-text search for each holding's watch topics, and for other filers naming it.",
  tdnet: "Tokyo Stock Exchange timely disclosures, matched to holdings listed in Japan by their stock code.",
  hkex: "HKEXnews announcements, matched to holdings listed in Hong Kong by stock code.",
  cninfo: "Shanghai and Shenzhen announcements, matched to holdings listed in China by their six-digit code.",
  dart: "Korean company disclosures, matched to holdings listed in Korea by stock code. Needs DART_API_KEY.",
  gazette: "The Gazette's notices (insolvency, winding-up) naming a holding listed in the UK.",
  filings: "EDINET and Companies House filings the disclosure jobs already stored for your holdings; no second fetch.",
};

export type SourceForm = {
  key: string;
  id: string;
  name: string;
  tier: 1 | 2 | 3;
  type: SourceType;
  url: string;
  enabled: boolean;
  tickers: string;
  /** A macro or central-bank source: kept without matching a holding, shown under Markets and macro. */
  macro: boolean;
  /** The saved source as it came, so fields the form has no input for (region, language, paywall, fetch policy) survive a save. */
  base: Partial<NewsSource>;
};

export type SourcesForm = {
  sources: SourceForm[];
  /** "id | label | kind | keyword, keyword" per line; a leading "off " disables the topic. */
  portfolio_topics: string;
  domain_tiers: string;
  blocked_domains: string;
  allowed_domains: string;
  unknown_outlet_tier: 1 | 2 | 3;
  freshness_hours: string;
  include_tier3: boolean;
  /** The Google News edition: interface language (en-US) and country (US). */
  google_news_language: string;
  google_news_country: string;
};

const SOURCE_FLAGS = { paywall: "open", state_media: false, needs_alt: false } as const;

/** The backend's defaults (news_sources.py), shown when nothing is saved yet (it fills in the same values). */
export const DEFAULT_SOURCES: NewsSourcesConfig = {
  schema_version: 1,
  sources: ([
    { id: "edgar-filings", name: "SEC EDGAR filings", tier: 1, type: "edgar", url: null, enabled: true, tickers: [], macro: false },
    { id: "edgar-full-text", name: "SEC EDGAR full-text search", tier: 1, type: "edgar_fts", url: null, enabled: true, tickers: [], macro: false },
    { id: "finnhub-company-news", name: "Finnhub company news", tier: 2, type: "finnhub", url: null, enabled: true, tickers: [], macro: false },
    { id: "google-news", name: "Google News", tier: 2, type: "google_news", url: null, enabled: false, tickers: [], macro: false },
    { id: "rns-investegate", name: "RNS (Investegate)", tier: 1, type: "rns", url: null, enabled: false, tickers: [], macro: false },
    { id: "eqs-news", name: "EQS News", tier: 1, type: "eqs", url: null, enabled: false, tickers: [], macro: false },
    { id: "fed-press", name: "Federal Reserve press releases", tier: 1, type: "rss", url: "https://www.federalreserve.gov/feeds/press_monetary.xml", enabled: true, tickers: [], macro: true },
    { id: "ecb-press", name: "ECB press releases", tier: 1, type: "rss", url: "https://www.ecb.europa.eu/rss/press.html", enabled: true, tickers: [], macro: true },
    { id: "cnb-press", name: "ČNB press releases", tier: 1, type: "rss", url: "https://www.cnb.cz/en/.content/rss-feed/rss-feed_tz.xml", enabled: true, tickers: [], macro: true },
    { id: "boj-whatsnew", name: "Bank of Japan what's new", tier: 1, type: "rss", url: "https://www.boj.or.jp/en/rss/whatsnew.xml", enabled: true, tickers: [], macro: true },
    { id: "boe-news", name: "Bank of England news", tier: 1, type: "rss", url: "https://www.bankofengland.co.uk/rss/news", enabled: true, tickers: [], macro: true },
    { id: "bundesbank-press", name: "Deutsche Bundesbank", tier: 1, type: "rss", url: "https://www.bundesbank.de/service/rss/en/633306/feed.rss", enabled: true, tickers: [], macro: true },
    { id: "boc-press", name: "Bank of Canada press releases", tier: 1, type: "rss", url: "https://www.bankofcanada.ca/content_type/press-releases/feed/", enabled: true, tickers: [], macro: true },
    { id: "tdnet", name: "TDnet (Tokyo)", tier: 1, type: "tdnet", url: null, enabled: false, tickers: [], macro: false },
    { id: "hkexnews", name: "HKEXnews", tier: 1, type: "hkex", url: null, enabled: false, tickers: [], macro: false },
    { id: "cninfo", name: "CNINFO (Shanghai, Shenzhen)", tier: 1, type: "cninfo", url: null, enabled: false, tickers: [], macro: false },
    { id: "dart", name: "DART (Korea)", tier: 1, type: "dart", url: null, enabled: false, tickers: [], macro: false },
    { id: "gazette", name: "The Gazette (UK)", tier: 1, type: "gazette", url: null, enabled: false, tickers: [], macro: false },
    { id: "filings-edinet-ch", name: "EDINET and Companies House filings", tier: 1, type: "filings", url: null, enabled: false, tickers: [], macro: false },
  ] as const).map((s) => ({ ...s, tickers: [], ...SOURCE_FLAGS })),
  portfolio_topics: [
    { id: "rates", label: "Interest rates", kind: "rates", keywords: ["interest rate", "rate cut", "rate hike", "central bank", "yield curve", "inflation"], enabled: true },
    { id: "trade-policy", label: "Tariffs and trade policy", kind: "regulation", keywords: ["tariff", "tariffs", "export controls", "sanctions", "trade war"], enabled: true },
    { id: "antitrust", label: "Antitrust and regulation", kind: "regulation", keywords: ["antitrust", "competition authority", "SEC investigation", "fined"], enabled: true },
    { id: "fx", label: "Currencies", kind: "currency", keywords: ["dollar", "euro", "yen", "yuan", "sterling", "currency"], enabled: true },
    { id: "semiconductors", label: "Semiconductor supply", kind: "supply_chain", keywords: ["semiconductor", "chip shortage", "foundry", "TSMC"], enabled: true },
  ],
  domain_tiers: [
    ...["sec.gov", "globenewswire.com", "prnewswire.com", "businesswire.com", "federalreserve.gov", "ecb.europa.eu", "cnb.cz"].map((domain) => ({ domain, tier: 1 as const })),
    ...["reuters.com", "apnews.com", "cnbc.com", "marketwatch.com", "ft.com", "bloomberg.com", "wsj.com", "hn.cz", "e15.cz", "seznamzpravy.cz"].map((domain) => ({ domain, tier: 2 as const })),
    ...["seekingalpha.com", "reddit.com", "x.com", "twitter.com", "medium.com"].map((domain) => ({ domain, tier: 3 as const })),
  ],
  blocked_domains: [],
  allowed_domains: [],
  unknown_outlet_tier: 3,
  freshness_hours: 72,
  include_tier3: false,
  google_news_locale: { language: "en-US", country: "US" },
};

export function newSource(): SourceForm {
  return { key: nextKey(), id: "", name: "", tier: 2, type: "rss", url: "", enabled: true, tickers: "", macro: false, base: { ...SOURCE_FLAGS } };
}

type PortfolioTopic = NonNullable<NewsSourcesConfig["portfolio_topics"]>[number];
export const TOPIC_KINDS = ["macro", "regulation", "rates", "currency", "sector", "supply_chain", "other"] as const;

export function formatPortfolioTopics(topics: readonly PortfolioTopic[]): string {
  return topics.map((t) => `${t.enabled === false ? "off " : ""}${t.id} | ${t.label} | ${t.kind ?? "other"} | ${t.keywords.join(", ")}`).join("\n");
}

/** Lines of "id | label | kind | keywords"; a half-typed kind is passed on for the backend to reject. */
export function parsePortfolioTopics(text: string): PortfolioTopic[] {
  return lines(text).map((line) => {
    const off = /^off\s+/i.test(line);
    const [id = "", label = "", kind = "other", keywords = ""] = line.replace(/^off\s+/i, "").split("|").map((p) => p.trim());
    return {
      id: id.toLowerCase(),
      label,
      kind: (kind || "other") as PortfolioTopic["kind"],
      keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean),
      enabled: !off,
    };
  });
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
    portfolio_topics: formatPortfolioTopics(b.portfolio_topics ?? defaults.portfolio_topics ?? []),
    sources: (b.sources ?? []).map((s) => ({
      key: nextKey(),
      id: s.id,
      name: s.name,
      tier: s.tier,
      type: s.type,
      url: s.url ?? "",
      enabled: s.enabled ?? true,
      tickers: (s.tickers ?? []).join("\n"),
      macro: s.macro ?? false,
      base: s,
    })),
    domain_tiers: (b.domain_tiers ?? []).map((d) => `${d.domain} ${d.tier}`).join("\n"),
    blocked_domains: (b.blocked_domains ?? []).join("\n"),
    allowed_domains: (b.allowed_domains ?? []).join("\n"),
    unknown_outlet_tier: b.unknown_outlet_tier ?? 3,
    freshness_hours: String(b.freshness_hours ?? 72),
    include_tier3: b.include_tier3 ?? false,
    google_news_language: b.google_news_locale?.language ?? "en-US",
    google_news_country: b.google_news_locale?.country ?? "US",
  };
}

export function sourcesToBody(form: SourcesForm): NewsSourcesConfig {
  return {
    schema_version: 1,
    portfolio_topics: parsePortfolioTopics(form.portfolio_topics),
    sources: form.sources.map((s) => ({
      ...SOURCE_FLAGS,
      ...s.base,
      id: s.id.trim(),
      name: s.name.trim(),
      tier: s.tier,
      type: s.type,
      url: PER_HOLDING.includes(s.type) ? null : s.url.trim() || null,
      enabled: s.enabled,
      tickers: lines(s.tickers),
      macro: s.macro,
    })),
    domain_tiers: parseDomainTiers(form.domain_tiers) as NewsSourcesConfig["domain_tiers"],
    blocked_domains: lines(form.blocked_domains).map((d) => d.toLowerCase()),
    allowed_domains: lines(form.allowed_domains).map((d) => d.toLowerCase()),
    unknown_outlet_tier: form.unknown_outlet_tier,
    freshness_hours: numberOrText(form.freshness_hours) as number,
    include_tier3: form.include_tier3,
    google_news_locale: {
      language: form.google_news_language.trim(),
      country: form.google_news_country.trim().toUpperCase(),
    },
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
  credit_stress: 2,
  trade_exposure: 2,
  patent_status: 1,
  other: 0,
};

export type OverrideForm = { key: string; ticker: string; event_type: EventType; severity: number };

export type AnnouncementService = "rns" | "eqs" | "tdnet" | "hkex" | "cninfo" | "dart" | "gazette" | "filings" | "any";
export type AnnouncementTarget = EventType | "classify";
export const ANNOUNCEMENT_SERVICES: readonly { value: AnnouncementService; label: string }[] = [
  { value: "any", label: "Any service" },
  { value: "rns", label: "RNS (UK)" },
  { value: "eqs", label: "EQS (Germany)" },
  { value: "tdnet", label: "TDnet (Japan)" },
  { value: "hkex", label: "HKEXnews (Hong Kong)" },
  { value: "cninfo", label: "CNINFO (China)" },
  { value: "dart", label: "DART (Korea)" },
  { value: "gazette", label: "The Gazette (UK)" },
  { value: "filings", label: "EDINET / Companies House filings" },
];
export type AnnouncementRuleForm = {
  key: string;
  service: AnnouncementService;
  pattern: string;
  event_type: AnnouncementTarget;
};

/**
 * The backend's default announcement-type mapping (apps/backend/src/steward/config/
 * event_rules.py, `_default_announcement_rules`), for when nothing is saved yet. First match
 * wins; "classify" leaves the item to the classifier.
 */
export const DEFAULT_ANNOUNCEMENT_RULES: readonly Omit<AnnouncementRuleForm, "key">[] = [
  { service: "any", pattern: String.raw`profit warning`, event_type: "guidance_change" },
  {
    service: "rns",
    pattern: String.raw`(final|interim|half[- ]?year|preliminary|q[1-4]|quarterly|full[- ]year) results|results for the|annual financial report|half[- ]year report`,
    event_type: "earnings_release",
  },
  { service: "rns", pattern: String.raw`trading (statement|update)|q[1-4] trading`, event_type: "earnings_release" },
  { service: "rns", pattern: String.raw`director/pdmr|pdmr shareholding|director.?s? dealing`, event_type: "insider_transaction" },
  {
    service: "rns",
    pattern: String.raw`offer for|possible offer|recommended (cash )?offer|rule 2\.[47]|scheme of arrangement|merger|acquisition|disposal`,
    event_type: "m_and_a",
  },
  {
    service: "rns",
    pattern: String.raw`transaction in own shares|buyback|buy-back|dividend declaration|scrip dividend|capital raise|placing`,
    event_type: "capital_allocation",
  },
  {
    service: "rns",
    pattern: String.raw`directorate change|board change|appointment of|ceo|chief executive|chief financial`,
    event_type: "management_change",
  },
  {
    service: "rns",
    pattern: String.raw`holding\(s\) in company|total voting rights|notice of agm|result of agm|block listing|form 8`,
    event_type: "other",
  },
  { service: "eqs", pattern: String.raw`ad-?hoc`, event_type: "classify" },
  { service: "eqs", pattern: String.raw`directors.? dealings`, event_type: "insider_transaction" },
  { service: "eqs", pattern: String.raw`voting rights`, event_type: "other" },
  {
    service: "eqs",
    pattern: String.raw`financial reports?|half-?year report|quarterly (report|statement)|annual report`,
    event_type: "earnings_release",
  },
  { service: "tdnet", pattern: String.raw`業績予想の修正|配当予想の修正|業績予想.{0,6}修正|下方修正|上方修正`, event_type: "guidance_change" },
  { service: "tdnet", pattern: String.raw`決算短信|四半期報告|有価証券報告|決算概要|決算補足`, event_type: "earnings_release" },
  { service: "tdnet", pattern: String.raw`自己株式|配当|株主還元|株式分割|新株予約権|公募|第三者割当`, event_type: "capital_allocation" },
  { service: "tdnet", pattern: String.raw`合併|買収|株式取得|公開買付|子会社化|株式交換|事業譲渡|吸収分割`, event_type: "m_and_a" },
  { service: "tdnet", pattern: String.raw`代表取締役|役員の異動|役員人事|人事異動`, event_type: "management_change" },
  { service: "hkex", pattern: String.raw`profit warning|positive profit alert|profit alert`, event_type: "guidance_change" },
  { service: "hkex", pattern: String.raw`(annual|interim|final|quarterly|third quarter|first quarter) results|results announcement`, event_type: "earnings_release" },
  { service: "hkex", pattern: String.raw`connected transaction|discloseable transaction|acquisition|disposal|takeover|general offer|merger|privatisation`, event_type: "m_and_a" },
  { service: "hkex", pattern: String.raw`repurchase|buy-?back|dividend|placing|rights issue|open offer|subscription of new shares|share scheme`, event_type: "capital_allocation" },
  { service: "hkex", pattern: String.raw`change of director|appointment|resignation|retirement|change in (chairman|chief)`, event_type: "management_change" },
  { service: "hkex", pattern: String.raw`dealings? by|director.?s? interest|disclosure of interests`, event_type: "insider_transaction" },
  { service: "cninfo", pattern: String.raw`业绩预告|业绩快报|盈利预警|业绩预增|业绩预减|业绩修正`, event_type: "guidance_change" },
  { service: "cninfo", pattern: String.raw`年度报告|半年度报告|季度报告|年报|中报|一季报|三季报|财务报告`, event_type: "earnings_release" },
  { service: "cninfo", pattern: String.raw`收购|重组|并购|要约|吸收合并|资产出售|资产购买`, event_type: "m_and_a" },
  { service: "cninfo", pattern: String.raw`回购|分红|利润分配|权益分派|增发|配股|可转债|定向增发`, event_type: "capital_allocation" },
  { service: "cninfo", pattern: String.raw`董事|监事|高级管理人员|辞职|聘任|总经理`, event_type: "management_change" },
  { service: "cninfo", pattern: String.raw`增持|减持|权益变动|持股`, event_type: "insider_transaction" },
  { service: "cninfo", pattern: String.raw`立案|处罚|诉讼|仲裁|问询函|监管函`, event_type: "regulatory_legal" },
  { service: "dart", pattern: String.raw`임원.{0,3}주요주주|특정증권등소유상황`, event_type: "insider_transaction" },
  { service: "dart", pattern: String.raw`영업\(잠정\)실적|매출액또는손익구조|분기보고서|반기보고서|사업보고서`, event_type: "earnings_release" },
  { service: "dart", pattern: String.raw`합병|인수|양수도|영업양수|타법인주식`, event_type: "m_and_a" },
  { service: "dart", pattern: String.raw`자기주식|배당|유상증자|무상증자|전환사채|신주인수권`, event_type: "capital_allocation" },
  { service: "dart", pattern: String.raw`대표이사|임원.{0,3}변경|최대주주변경`, event_type: "management_change" },
  { service: "gazette", pattern: String.raw`winding.?up|liquidat|administrat|receiver|insolven|strike.?off|dissol`, event_type: "entity_status" },
  { service: "filings", pattern: String.raw`臨時報告書`, event_type: "classify" },
  { service: "filings", pattern: String.raw`有価証券報告書|四半期報告書|半期報告書|決算`, event_type: "earnings_release" },
  { service: "filings", pattern: String.raw`大量保有報告書|変更報告書`, event_type: "insider_transaction" },
  { service: "filings", pattern: String.raw`accounts|annual report`, event_type: "earnings_release" },
  { service: "filings", pattern: String.raw`appointment of|termination of appointment|change of (director|secretary)`, event_type: "management_change" },
  { service: "filings", pattern: String.raw`allotment|statement of capital|capital|resolution|memorandum`, event_type: "capital_allocation" },
  { service: "any", pattern: String.raw`.`, event_type: "classify" },
];

/** The backend's default macro surprise bands, per macro series key (in the series' own unit). */
export const DEFAULT_MACRO_SURPRISE_BANDS: Record<string, number> = {
  hicp: 0.3,
  unemployment: 0.3,
  gdp: 0.5,
  industrial_production: 2.0,
  sentiment: 3.0,
  us_cpi: 0.3,
  us_unemployment: 0.2,
  ecb_deposit_rate: 0.0,
  fed_funds: 0.0,
};

export type EventRulesForm = {
  severities: Record<EventType, number>;
  overrides: OverrideForm[];
  escalate_when_thesis_linked: EventType[];
  earnings_check_on: EventType[];
  earnings_check_cooldown_days: string;
  thesis_breach_severity: number;
  insider_cluster_count: string;
  insider_cluster_days: string;
  recycled_after_days: string;
  price_move_threshold_pct: string;
  /** "series_key band" per line, e.g. "hicp 0.3". */
  macro_surprise_bands: string;
  announcement_types: AnnouncementRuleForm[];
};

export function newOverride(): OverrideForm {
  return { key: nextKey(), ticker: "", event_type: "product_business", severity: 2 };
}

export function newAnnouncementRule(): AnnouncementRuleForm {
  return { key: nextKey(), service: "rns", pattern: "", event_type: "classify" };
}

/** "hicp 0.3" per line <-> {hicp: 0.3}. A half-typed band stays text for the backend to reject. */
export function parseSurpriseBands(text: string): Record<string, number | string> {
  return Object.fromEntries(
    lines(text).map((line) => {
      const [key, band = ""] = line.split(/\s+/);
      return [key.toLowerCase(), numberOrText(normaliseSeparators(band))];
    }),
  );
}

export function formatSurpriseBands(bands: Record<string, number>): string {
  return Object.entries(bands)
    .map(([key, band]) => `${key} ${band}`)
    .join("\n");
}

export function eventRulesFromBody(body: EventRulesConfig | null): EventRulesForm {
  return {
    severities: { ...DEFAULT_SEVERITIES, ...(body?.severities as Partial<Record<EventType, number>> | undefined) },
    overrides: (body?.overrides ?? []).map((o) => ({ key: nextKey(), ...o })),
    escalate_when_thesis_linked: [...(body?.escalate_when_thesis_linked ?? ["regulatory_legal"])],
    earnings_check_on: [...(body?.earnings_check_on ?? ["earnings_release"])],
    earnings_check_cooldown_days: String(body?.earnings_check_cooldown_days ?? 10),
    thesis_breach_severity: body?.thesis_breach_severity ?? 3,
    insider_cluster_count: String(body?.insider_cluster_count ?? 3),
    insider_cluster_days: String(body?.insider_cluster_days ?? 14),
    recycled_after_days: String(body?.recycled_after_days ?? 7),
    price_move_threshold_pct: String(body?.price_move_threshold_pct ?? 8),
    macro_surprise_bands: formatSurpriseBands(body?.macro_surprise_bands ?? DEFAULT_MACRO_SURPRISE_BANDS),
    announcement_types: (body?.announcement_types ?? DEFAULT_ANNOUNCEMENT_RULES).map((r) => ({
      key: nextKey(),
      service: r.service ?? "any",
      pattern: r.pattern,
      event_type: r.event_type,
    })),
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
    earnings_check_cooldown_days: numberOrText(form.earnings_check_cooldown_days) as number,
    thesis_breach_severity: form.thesis_breach_severity,
    insider_cluster_count: numberOrText(form.insider_cluster_count) as number,
    insider_cluster_days: numberOrText(form.insider_cluster_days) as number,
    recycled_after_days: numberOrText(form.recycled_after_days) as number,
    price_move_threshold_pct: numberOrText(normaliseSeparators(form.price_move_threshold_pct)) as number,
    macro_surprise_bands: parseSurpriseBands(form.macro_surprise_bands) as Record<string, number>,
    // The pattern is a regular expression: sent as typed (only trailing spaces trimmed).
    announcement_types: form.announcement_types.map((r) => ({
      service: r.service,
      pattern: r.pattern.trimEnd(),
      event_type: r.event_type,
    })),
  };
}
