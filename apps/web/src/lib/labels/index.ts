import { SCHEDULES, scheduleLabel } from "@/lib/config/jobs-form";

/*
 * LabelMap: one place that turns machine keys (XBRL members, macro series ids, strategy rule
 * ids, job keys, provider names) into words. `label(kind, key)` never returns the raw key:
 * an unknown key falls back to `humanise`.
 */

export type LabelKind = "segment" | "macro" | "rule" | "job" | "provider";

/** "IPhoneMember" -> "iPhone"; "us_10y" -> "Us 10y"; "max-country" -> "Max country". */
export function humanise(key: string): string {
  const base = key
    .trim()
    .replace(/^[A-Za-z-]+:(?=\w)/, "") // "us-gaap:" / "rule:" prefix
    .replace(/(Member|Segment|Axis)$/, "")
    .replace(/[_\-.]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim();
  if (base === "") return key.trim();
  return base.charAt(0).toUpperCase() + base.slice(1);
}

// ---- XBRL segment members ------------------------------------------------------------

const SEGMENT_LABELS: Record<string, string> = {
  IPhoneMember: "iPhone",
  IPadMember: "iPad",
  MacMember: "Mac",
  ServiceMember: "Services",
  ServicesMember: "Services",
  ProductMember: "Products",
  ProductsMember: "Products",
  WearablesHomeandAccessoriesMember: "Wearables, Home and Accessories",
  AmericasSegmentMember: "Americas",
  EuropeSegmentMember: "Europe",
  GreaterChinaSegmentMember: "Greater China",
  JapanSegmentMember: "Japan",
  RestOfAsiaPacificSegmentMember: "Rest of Asia Pacific",
  IntersegmentEliminationMember: "Intersegment eliminations",
  CorporateNonSegmentMember: "Corporate and unallocated",
  AllOtherSegmentsMember: "All other segments",
  OperatingSegmentsMember: "Operating segments",
  NonUsMember: "Outside the US",
  UnitedStatesMember: "United States",
};

// ---- macro series ids -----------------------------------------------------------------

const MACRO_LABELS: Record<string, string> = {
  us_cpi: "US inflation (CPI)",
  us_unemployment: "US unemployment",
  us_hy_spread: "US high-yield credit spread",
  fed_funds: "Fed funds rate",
  us_10y: "US 10-year Treasury yield",
  ecb_deposit_rate: "ECB deposit rate",
  euro_area_private_credit: "Euro-area private credit",
  euro_area_10y_yield: "Euro-area 10-year yield",
  euro_area_m3_growth: "Euro-area M3 growth",
  euro_area_household_loan_rate: "Euro-area household loan rate",
  euro_area_corporate_loan_rate: "Euro-area corporate loan rate",
  ea_household_overnight_deposit_rate: "Euro-area household overnight deposit rate",
  commodity_copper: "Copper",
  commodity_aluminum: "Aluminium",
  commodity_nickel: "Nickel",
  commodity_zinc: "Zinc",
  commodity_tin: "Tin",
  commodity_iron_ore: "Iron ore",
  commodity_uranium: "Uranium",
  commodity_wti: "Crude oil (WTI)",
  commodity_brent: "Crude oil (Brent)",
  commodity_gas_us: "Natural gas (US, Henry Hub)",
  commodity_gas_eu: "Natural gas (EU)",
  commodity_wheat: "Wheat",
  commodity_maize: "Maize",
  commodity_cotton: "Cotton",
  commodity_rubber: "Rubber",
  commodity_coffee: "Coffee",
};

// ---- strategy rule ids ----------------------------------------------------------------

const RULE_LABELS: Record<string, string> = {
  "listing-exact-match": "Exact listing match",
  "calibrated-refresh": "Calibrated refresh",
  always: "Always accept",
  "anonymous-label": "Anonymous counterparty label",
  two_domains_agree: "Two sources agree",
  x: "Automatic rule",
};

/**
 * "max-country:geography:Unclassified" -> "Country limit: Unclassified"; "min-cash:cash" ->
 * "Minimum cash"; a "rule:" prefix is accepted and dropped. The id comes first, then the
 * dimension it measures, then the bucket that breached it.
 */
function ruleLabel(raw: string): string {
  const key = raw.replace(/^rule:/, "");
  const [id, , ...bucketParts] = key.split(":");
  const bucket = bucketParts.join(":").trim();
  let name = lookup(RULE_LABELS, key) ?? lookup(RULE_LABELS, id);
  if (!name) {
    const m = /^(max|min)[-_](.+)$/.exec(id);
    if (m) {
      const what = humanise(m[2]).toLowerCase();
      name = m[1] === "max" ? `${humanise(what)} limit` : `Minimum ${what}`;
    } else {
      name = humanise(id);
    }
  }
  return bucket ? `${name}: ${bucket}` : name;
}

// ---- data providers -------------------------------------------------------------------

const PROVIDER_LABELS: Record<string, string> = {
  twelvedata: "Twelve Data",
  twelve_data: "Twelve Data",
  fmp: "Financial Modeling Prep",
  yahoo: "Yahoo Finance",
  sec: "SEC EDGAR",
  edgar: "SEC EDGAR",
  edinet: "EDINET (Japan)",
  companies_house: "Companies House",
  alpha_vantage: "Alpha Vantage",
  finnhub: "Finnhub",
  fred: "FRED",
  ecb: "European Central Bank",
  world_bank: "World Bank",
  un_comtrade: "UN Comtrade",
  frankfurter: "Frankfurter",
  cnb: "Czech National Bank",
  gleif: "GLEIF",
  openfigi: "OpenFIGI",
  ishares: "iShares",
  vanguard: "Vanguard",
  issuer_ir: "Company investor relations",
  pdl: "People Data Labs",
  diffbot: "Diffbot",
  openai: "OpenAI",
  google: "Google",
  anthropic: "Anthropic",
  trading212: "Trading 212",
};

const OTHER_JOB_LABELS: Record<string, string> = {
  earnings_check: "Earnings check",
  thesis_verdicts: "Thesis verdicts",
  event_escalation: "Event escalation",
  research: "Research",
  chat: "Chat answer",
  inbox_agent: "Inbox agent",
};

function lookup(map: Record<string, string>, key: string): string | undefined {
  return Object.prototype.hasOwnProperty.call(map, key) ? map[key] : undefined;
}

export function label(kind: LabelKind, key: string): string {
  if (key === "") return key;
  switch (kind) {
    case "segment":
      return lookup(SEGMENT_LABELS, key) ?? humanise(key);
    case "macro":
      return lookup(MACRO_LABELS, key) ?? humanise(key);
    case "rule":
      return ruleLabel(key);
    case "provider":
      return lookup(PROVIDER_LABELS, key.toLowerCase()) ?? humanise(key);
    case "job":
      return lookup(OTHER_JOB_LABELS, key) ?? scheduleLabel(key);
  }
}

/**
 * Free text from the backend with the machine keys inside it replaced: `rule:` ids, and
 * snake_case tokens that are known job or macro series keys. Anything else is left alone.
 */
export function labelText(text: string): string {
  return text
    .replace(/\brule:[A-Za-z0-9_.:-]*[A-Za-z0-9_]/g, (m) => label("rule", m))
    .replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g, (m) => {
      if (lookup(MACRO_LABELS, m)) return label("macro", m);
      if (lookup(OTHER_JOB_LABELS, m) || m in SCHEDULES) return label("job", m);
      return m;
    });
}

/** `label` for a string that may already be words: a machine key is mapped, text is kept. */
export function labelIfKey(kind: LabelKind, s: string): string {
  return looksLikeKey(s) ? label(kind, s) : s;
}

/** True for a string that looks like a machine key rather than words (`IPhoneMember`, `us_10y`). */
export function looksLikeKey(s: string): boolean {
  if (/\s/.test(s)) return false;
  return /^[A-Za-z0-9]+(?:[_:.-][A-Za-z0-9]+)+$/.test(s) || /^[A-Z][A-Za-z0-9]*Member$/.test(s);
}
