import type { PortfolioRisk } from "@/lib/api/client";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { formatDate } from "@/lib/format";

/*
 * Labels for the Sharpe ratio from GET /portfolio/risk (all *_rate / *_return fields are
 * fractions). The backend computes every figure; this only words them.
 */

const DAY = 86_400_000;

/** "Sharpe · 1Y", or "Sharpe · since 3 Feb 2026" when the window covers less than a year. */
export function sharpeLabel(risk: Pick<PortfolioRisk, "as_of" | "days" | "portfolio">): string {
  const start = risk.portfolio.window_start;
  const end = risk.as_of ?? risk.portfolio.window_end;
  if (start && end) {
    const covered = (Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / DAY;
    // Weekends at either end: a full year starts within a few days of as_of − days.
    if (covered < risk.days - 7) return `Sharpe · since ${formatDate(start)}`;
  }
  return risk.days === 365 ? "Sharpe · 1Y" : `Sharpe · ${risk.days}d`;
}

/** "CZEONIA" for the ČNB series, else the stored label. */
export function riskFreeName(rf: NonNullable<PortfolioRisk["risk_free"]>): string {
  if (rf.series_id === "czeonia") return "CZEONIA";
  if (rf.series_id === "cnb_repo_2w") return "ČNB 2W repo";
  return rf.label;
}

/** "rf: CZEONIA 3.56%" (the latest stored fixing), or null without a rate. */
export function riskFreeShort(risk: Pick<PortfolioRisk, "risk_free">): string | null {
  const rf = risk.risk_free;
  if (!rf || rf.latest_rate === null) return null;
  return `rf: ${riskFreeName(rf)} ${formatPct(rf.latest_rate, 2)}`;
}

/** The Sharpe ratio to two decimals, "—" when there is none. */
export function formatSharpe(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : formatNumber(value, 2);
}

/** The tooltip: method, window, observations, the risk-free source with its as-of date. */
export function sharpeExplanation(risk: PortfolioRisk): string {
  const p = risk.portfolio;
  const lines = [
    "Sharpe ratio: how much return above a risk-free CZK deposit the portfolio earned per unit of volatility.",
    "Mean daily excess return ÷ standard deviation of daily excess returns × √252. Returns are time-weighted (deposits and withdrawals netted out), weekdays only, in the base currency.",
  ];
  const rf = risk.risk_free;
  if (rf) {
    lines.push(
      `Risk-free: ${rf.label} from the Czech National Bank${rf.fallback ? " (standing in: CZEONIA not collected yet)" : ""}, previous day's rate ÷ 100 ÷ 252, carried over weekends and holidays; latest ${
        rf.latest_rate !== null ? formatPct(rf.latest_rate, 2) : "—"
      }${rf.latest_date ? ` for ${formatDate(rf.latest_date)}` : ""}${p.risk_free_average !== null ? `, window average ${formatPct(p.risk_free_average, 2)}` : ""}.`,
    );
  }
  if (p.window_start && p.window_end) {
    lines.push(`Window ${formatDate(p.window_start)} – ${formatDate(p.window_end)}, ${p.observations} daily returns.`);
  }
  if (p.annualised_volatility !== null && p.mean_excess_return !== null) {
    lines.push(`Annualised volatility ${formatPct(p.annualised_volatility, 1)}, mean excess return ${formatPct(p.mean_excess_return, 1, true)} a year.`);
  }
  const b = risk.benchmark;
  if (b && risk.benchmark_symbol) {
    lines.push(
      b.sharpe !== null
        ? `Benchmark ${risk.benchmark_symbol} in ${b.currency ?? "the base currency"}: Sharpe ${formatSharpe(b.sharpe)}, volatility ${formatPct(b.annualised_volatility ?? 0, 1)}.`
        : `Benchmark ${risk.benchmark_symbol}: ${b.reason ?? "no ratio"}.`,
    );
  }
  lines.push("A few months of history make this a noisy estimate.");
  return lines.join("\n");
}
