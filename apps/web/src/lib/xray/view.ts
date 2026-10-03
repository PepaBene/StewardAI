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
