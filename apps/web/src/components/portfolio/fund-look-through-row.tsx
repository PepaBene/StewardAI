"use client";

import { ChevronDownIcon } from "lucide-react";

import { formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { formatMoney } from "@/lib/format";
import { countryLabel, fundProvenance, type FundLookThrough } from "@/lib/portfolio/look-through";
import { cn } from "@/lib/utils";

/** The disclosure button on an ETF row of the holdings table. */
export function FundExpandButton({
  open,
  onToggle,
  controls,
  name,
}: {
  open: boolean;
  onToggle: () => void;
  /** id of the row it shows. */
  controls: string;
  name: string;
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      title={open ? "Hide underlying holdings" : "Show underlying holdings"}
      className="inline-flex shrink-0 items-center gap-1 border border-border px-1.5 py-0.5 text-xs font-medium text-text-muted hover:text-text focus-visible:outline-2 focus-visible:outline-focus"
    >
      <span className="sr-only">{name}: </span>
      Holdings
      <ChevronDownIcon aria-hidden="true" className={cn("size-3 transition-transform", open && "rotate-180")} />
    </button>
  );
}

function Honest({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-text-muted">{children}</p>;
}

/**
 * The expanded row under an ETF: its top holdings from the latest stored issuer snapshot,
 * with each line's weight in the fund and in the portfolio. Honest states when the fund has
 * no snapshot yet, no look-through source, or the data is still loading.
 */
export function FundLookThroughRow({
  id,
  fund,
  loading,
  error,
  colSpan,
  currency,
}: {
  id: string;
  fund: FundLookThrough | null;
  /** GET /portfolio/look-through has not answered yet. */
  loading: boolean;
  /** It failed (and there is no earlier answer to show). */
  error: string | null;
  colSpan: number;
  currency: string;
}) {
  let body: React.ReactNode;
  if (loading) {
    body = <Honest>Loading underlying holdings…</Honest>;
  } else if (error) {
    body = <Honest>Could not load the look-through ({error}).</Honest>;
  } else if (!fund || fund.status === "not_configured") {
    body = (
      <Honest>
        No look-through source is configured for this fund. Add its issuer and fund id under Config → Jobs → look-through funds
        (iShares and Vanguard are supported).
      </Honest>
    );
  } else if (!fund.as_of) {
    body = (
      <Honest>
        No holdings snapshot of this fund yet: the weekly ETF look-through job fetches it from {fund.provider === "vanguard" ? "Vanguard" : "iShares"}.
        {fund.last_attempt && !fund.last_attempt.ok
          ? ` The last attempt (${formatDay(fund.last_attempt.checked_at)}) failed: ${fund.last_attempt.error ?? "no error recorded"}.`
          : null}
      </Honest>
    );
  } else {
    body = (
      <div className="flex flex-col gap-2">
        <p className="text-xs text-text-muted">
          Top {fund.top_holdings.length} of {fund.holdings_count ?? "?"} equity holdings · {fundProvenance(fund)} · {fund.source_label}
          {fund.equity_share != null ? ` · ${formatPct(fund.equity_share, 1)} in equities, the rest cash & derivatives` : null}
        </p>
        <table className="w-full min-w-[40rem] text-xs">
          <caption className="sr-only">Top holdings of {fund.name ?? fund.label}</caption>
          <thead>
            <tr className="border-b border-border text-xs text-text-muted">
              <th scope="col" className="py-1.5 pr-3 text-left font-medium">Holding</th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">Sector</th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">Country</th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">In fund</th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">Of portfolio</th>
              <th scope="col" className="py-1.5 pl-3 text-right font-medium">Value ({currency})</th>
            </tr>
          </thead>
          <tbody>
            {fund.top_holdings.map((h, i) => (
              <tr key={`${h.isin ?? h.name}-${i}`} className="border-b border-border-soft last:border-b-0">
                <th scope="row" className="py-1 pr-3 text-left font-normal">
                  <span className="font-medium text-text">{h.name}</span>
                  {h.ticker ? <span className="ml-1.5 font-mono text-xs text-text-muted">{h.ticker}</span> : null}
                </th>
                <td className="px-3 py-1 text-text-muted">{h.sector ?? "—"}</td>
                <td className="px-3 py-1 text-text-muted">{countryLabel(h.country)}</td>
                <td className="num px-3 py-1 text-right">{formatPct(h.weight_in_fund, 2)}</td>
                <td className="num px-3 py-1 text-right">{formatPct(h.portfolio_weight, 2)}</td>
                <td className="num py-1 pl-3 text-right">{formatMoney(h.value_base, currency, { decimals: 0 })}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {fund.warnings.length ? <p className="text-xs text-text-faint">Source notes: {fund.warnings.join("; ")}</p> : null}
      </div>
    );
  }
  return (
    <tr id={id} className="border-b border-border-soft bg-surface-muted/60">
      <td colSpan={colSpan} className="py-3 pr-5 pl-[4.25rem]">
        {body}
      </td>
    </tr>
  );
}
