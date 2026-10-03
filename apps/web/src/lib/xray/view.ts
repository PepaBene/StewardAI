import { formatTickerLabel } from "./ticker-label";

export const TABS = [
  { id: "exposure", label: "Exposure", hint: "Issuer, sector and geography exposure with funds opened up. Weights are shares of total portfolio value; unknown fund weight is never spread over known holdings." },
  { id: "funds", label: "Funds", hint: "Fund overlap, concentration, fees and the fund-characteristics audit that separates real changes from parser drift." },
  { id: "coverage", label: "Coverage", hint: "Where each fund's and index's constituents come from, and which benchmarks are unsafe for performance or backtests." },
  { id: "ownership", label: "Ownership", hint: "Who holds a company: 13F institutions, N-PORT funds and 13D/G control stakes are separate channels and are never added together." },
  { id: "insiders", label: "Insiders", hint: "Insider transactions by economic class (US Forms 3/4/5) and UK PDMR notifications." },
] as const;

export type TabId = (typeof TABS)[number]["id"];

const FUND_TYPE = /\b(etp|etf|etn|fund|unit)\b|exchange traded/i;

export type HoldingChoice = { ticker: string; label: string };

/** Directly held companies: funds are excluded because 13F, 13D/G and Form 4 describe issuers, not funds. */
export function holdingChoices(holdings: readonly { ticker: string; name: string | null; security_type: string | null }[]): HoldingChoice[] {
  return holdings
    .filter((h) => !FUND_TYPE.test(h.security_type ?? ""))
    .map((h) => ({ ticker: h.ticker, label: formatTickerLabel(h.ticker, h.name) }));
}

/**
 * An `as_of` taken from the URL: a real calendar date, not in the future. Anything else is
 * dropped (the page then shows the latest) instead of being sent to the API as a 422.
 */
export function validAsOf(value: string | null | undefined, today: Date = new Date()): string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return "";
  const d = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== value) return "";
  return value > today.toISOString().slice(0, 10) ? "" : value;
}
