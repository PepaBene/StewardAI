import type { components } from "@/lib/api/schema";

type S = components["schemas"];
export type RiskReport = S["RiskReport"];
export type AnalyticsFigure = S["AnalyticsResult"];
export type ShockSet = S["ShockSet"];
export type ShockAssumptions = S["ShockAssumptions"];
export type ShockRun = S["ShockRun"];
export type ShockResult = S["ShockScenarioResult"];
export type ThemeExposure = S["ThemeExposure"];
export type ThemeSelector = S["ThemeSelector"];
export type RebalanceResponse = S["RebalanceResponse"];
export type RebalanceRequest = S["RebalanceRequest"];
export type PretradeResult = S["PretradeResult"];
export type PretradeCheck = S["PretradeCheck"];
export type TradeLeg = S["TradeLeg"];
export type WhatIfCase = S["WhatIfCase"];
export type WhatIfResult = S["WhatIfResult"];
export type SavedItem = S["SavedAnalysisItem"];
export type SavedKind = SavedItem["kind"];

/** A typed percentage ("-12.5", "12,5 %") as a fraction; blank is `null`, junk is `undefined`. */
export function parsePercent(text: string): number | null | undefined {
  const cleaned = text.trim().replace(/%$/, "").trim().replace(",", ".");
  if (cleaned === "") return null;
  if (!/^[-+−]?\d*\.?\d+$/.test(cleaned) && !/^[-+−]?\d+\.$/.test(cleaned)) return undefined;
  const value = Number(cleaned.replace("−", "-"));
  return Number.isFinite(value) ? value / 100 : undefined;
}

/** A typed plain number; blank is `null`, junk is `undefined`. */
export function parseNumber(text: string): number | null | undefined {
  const cleaned = text.trim().replace(/\s/g, "").replace(",", ".").replace("−", "-");
  if (cleaned === "") return null;
  const value = Number(cleaned);
  return Number.isFinite(value) ? value : undefined;
}

export type KeyedParse = { values: Record<string, number>; problems: string[] };

/**
 * Lines of "KEY value" (or "KEY: value", "KEY=value") into a map. `percent` reads the value as a
 * percentage and returns a fraction. Keys are upper-cased unless `keepCase` is set.
 */
export function parseKeyed(text: string, { percent, keepCase }: { percent: boolean; keepCase?: boolean }): KeyedParse {
  const values: Record<string, number> = {};
  const problems: string[] = [];
  for (const raw of text.split(/\r?\n|;/)) {
    const line = raw.trim();
    if (line === "") continue;
    const match = /^(.+?)\s*[:=\s]\s*([-+−]?[\d.,]+\s*%?)$/.exec(line);
    if (!match) {
      problems.push(`“${line}” is not a name followed by a number.`);
      continue;
    }
    const key = keepCase ? match[1].trim() : match[1].trim().toUpperCase();
    const value = percent ? parsePercent(match[2]) : parseNumber(match[2].replace("%", ""));
    if (value === null || value === undefined) {
      problems.push(`“${line}” has no usable number.`);
      continue;
    }
    values[key] = value;
  }
  return { values, problems };
}

export type ShockForm = {
  equity: string;
  rate: string;
  credit: string;
  fx: string;
  sector: string;
  country: string;
  unmeasuredBeta: string;
  betas: string;
  durations: string;
  spreadDurations: string;
};

export const EMPTY_SHOCK_FORM: ShockForm = {
  equity: "",
  rate: "",
  credit: "",
  fx: "",
  sector: "",
  country: "",
  unmeasuredBeta: "1",
  betas: "",
  durations: "",
  spreadDurations: "",
};

export type ShockBody = { shocks: ShockSet; assumptions: ShockAssumptions };

/** The form into the request body, or the first thing wrong with it. */
export function buildShock(form: ShockForm): { body: ShockBody } | { error: string } {
  const equity = parsePercent(form.equity);
  const rate = parseNumber(form.rate);
  const credit = parseNumber(form.credit);
  const beta = parseNumber(form.unmeasuredBeta);
  if (equity === undefined) return { error: "The equity move must be a percentage such as -20." };
  if (rate === undefined) return { error: "The rate change must be basis points such as 100." };
  if (credit === undefined) return { error: "The credit spread change must be basis points such as 200." };
  if (beta === undefined) return { error: "The beta for holdings without a measured one must be a number, or blank to leave them out." };
  const fx = parseKeyed(form.fx, { percent: true });
  const sector = parseKeyed(form.sector, { percent: true, keepCase: true });
  const country = parseKeyed(form.country, { percent: true });
  const betas = parseKeyed(form.betas, { percent: false });
  const durations = parseKeyed(form.durations, { percent: false });
  const spreads = parseKeyed(form.spreadDurations, { percent: false });
  const problem = [fx, sector, country, betas, durations, spreads].flatMap((p) => p.problems)[0];
  if (problem) return { error: problem };
  const shocks: ShockSet = {
    equity_pct: equity,
    rate_bp: rate,
    credit_bp: credit,
    fx: fx.values,
    sector: sector.values,
    country: country.values,
  };
  const empty =
    equity === null && rate === null && credit === null && [fx, sector, country].every((p) => Object.keys(p.values).length === 0);
  if (empty) return { error: "Enter at least one move to apply." };
  return {
    body: {
      shocks,
      assumptions: {
        equity_beta: betas.values,
        unmeasured_equity_beta: beta,
        duration_years: durations.values,
        spread_duration_years: spreads.values,
      },
    },
  };
}

/** Trade legs typed one per line as "buy VUAA 1500" or "sell CSPX 800". */
export function parseLegs(text: string): { legs: TradeLeg[] } | { error: string } {
  const legs: TradeLeg[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line === "") continue;
    const match = /^(buy|sell)\s+(\S+)\s+([\d.,]+)$/i.exec(line);
    const value = match ? parseNumber(match[3]) : undefined;
    if (!match || value === null || value === undefined || value <= 0) {
      return { error: `“${line}” should read like “buy VUAA 1500”: buy or sell, the ticker, an amount in your base currency.` };
    }
    legs.push({ side: match[1].toLowerCase() as TradeLeg["side"], ticker: match[2].toUpperCase(), value_base: value });
  }
  if (legs.length === 0) return { error: "Add at least one trade." };
  return { legs };
}

/** Printable names for the factors a stress test splits the loss into. */
const FACTOR: Record<string, string> = {
  equity: "Equity market",
  rates: "Interest rates",
  rate: "Interest rates",
  credit: "Credit spreads",
  fx: "Currency",
  sector: "Sector",
  country: "Country",
  interaction: "Combined effect",
};

export function factorLabel(key: string): string {
  return FACTOR[key] ?? key;
}

const STAT_LABEL: Record<string, string> = {
  volatility: "Volatility, annualised",
  downside_deviation: "Downside deviation, annualised",
  max_drawdown: "Largest drawdown",
  var_95: "Daily value at risk, 95%",
  es_95: "Daily expected shortfall, 95%",
  var_99: "Daily value at risk, 99%",
  es_99: "Daily expected shortfall, 99%",
  sharpe: "Sharpe ratio",
  sortino: "Sortino ratio",
  beta: "Beta to the benchmark",
  tracking_error: "Tracking error, annualised",
  correlation_benchmark: "Correlation with the benchmark",
};

export const STAT_GROUPS: { title: string; keys: string[] }[] = [
  { title: "How much it moves", keys: ["volatility", "downside_deviation", "max_drawdown"] },
  { title: "What a bad day costs", keys: ["var_95", "es_95", "var_99", "es_99"] },
  { title: "Return for the risk", keys: ["sharpe", "sortino"] },
  { title: "Against the benchmark", keys: ["beta", "tracking_error", "correlation_benchmark"] },
];

export function statLabel(key: string): string {
  return STAT_LABEL[key] ?? key;
}

/** Whether a statistic is a ratio shown as a plain number rather than a percentage. */
export function isRatio(key: string): boolean {
  return ["sharpe", "sortino", "beta", "correlation_benchmark"].includes(key);
}

/** The status each pre-trade check gets in the list, worst first. */
export const CHECK_ORDER: Record<PretradeCheck["status"], number> = { flag: 0, unavailable: 1, info: 2, pass: 3 };

export function sortChecks(checks: readonly PretradeCheck[]): PretradeCheck[] {
  return [...checks].sort((a, b) => CHECK_ORDER[a.status] - CHECK_ORDER[b.status]);
}

/** The loss ladder's bar length as a share of the largest absolute move in the list. */
export function ladderShare(value: number, largest: number): number {
  if (!Number.isFinite(value) || largest <= 0) return 0;
  return Math.min(1, Math.abs(value) / largest);
}
