import Link from "next/link";

import { AlertMessage } from "@/components/alert-message";
import { DivergingBar, type DivergingBarRow } from "@/components/charts";
import { DriftBar } from "@/components/portfolio/drift-bar";
import { DriftStatusBadge, isOutOfBand } from "@/components/portfolio/drift-status-badge";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { PortfolioOverview } from "@/lib/api/client";
import { formatPercent, formatPp } from "@/lib/format";
import { shortTicker } from "@/lib/portfolio/ticker";
import { cn } from "@/lib/utils";

function NoStrategy() {
  return (
    <AlertMessage tone="info" title="No strategy saved yet">
      <p>
        Drift needs target weights from the strategy config.{" "}
        <Link href="/config/strategy" className="text-accent-ink hover:underline">
          Set targets in the strategy editor
        </Link>
        .
      </p>
    </AlertMessage>
  );
}

function split(data: PortfolioOverview) {
  const drift = data.drift;
  const targeted = drift?.positions.filter((p) => p.status !== "no_target") ?? [];
  const untargeted = drift?.positions.filter((p) => p.status === "no_target") ?? [];
  const cash = drift?.cash;
  const cashRow = cash && cash.status !== "no_target" ? cash : null;
  return { drift, targeted, untargeted, cashRow };
}

/** Drift against target bands as diverging bars (percentage points), with the out-of-band count. */
export function DriftPanel({ data }: { data: PortfolioOverview }) {
  const { drift, targeted, cashRow } = split(data);
  const rows: DivergingBarRow[] = [
    ...targeted.map((p) => ({
      label: shortTicker(p.ticker),
      value: p.drift === null ? null : p.drift * 100,
      band: p.band === null ? undefined : p.band * 100,
    })),
    ...(cashRow
      ? [{ label: "Cash", value: cashRow.drift === null ? null : cashRow.drift * 100, band: cashRow.band === null ? undefined : cashRow.band * 100 }]
      : []),
  ];
  const out = drift?.out_of_band ?? 0;
  return (
    <Blueprint className="flex min-w-0 flex-col gap-3.5 px-6 py-5.5" aria-label="Drift against target bands">
      <PanelHeading
        kicker="Drift"
        title="Against target bands"
        action={
          drift && rows.length ? (
            <span className={cn("px-2 py-0.5 text-[11px] font-semibold", out > 0 ? "bg-accent text-accent-foreground" : "border border-border text-text-muted")}>
              {out > 0 ? `${out} outside band` : "All inside band"}
            </span>
          ) : null
        }
      />
      {data.strategy_config_version_id === null ? (
        <NoStrategy />
      ) : rows.length === 0 ? (
        <p className="text-sm text-text-muted">The strategy sets no position or cash targets.</p>
      ) : (
        <DivergingBar rows={rows} label="Drift from target, percentage points, with each tolerance band" />
      )}
      {drift && rows.length ? (
        <p className="text-xs text-text-muted">
          Weights are of {drift.weight_basis === "invested" ? "invested value" : "total value incl. cash"}; the shaded band is each
          target&rsquo;s tolerance.
        </p>
      ) : null}
    </Blueprint>
  );
}

/** Every targeted position and cash: weight, target and band, drift and status. */
export function DriftTable({ data }: { data: PortfolioOverview }) {
  const { drift, targeted, untargeted, cashRow } = split(data);
  return (
    <Blueprint className="flex min-w-0 flex-col gap-4 px-6 py-5.5" aria-labelledby="drift-table-heading">
      <PanelHeading
        kicker="Drift"
        title={<span id="drift-table-heading">Weights against targets</span>}
        action={
          drift && drift.out_of_band > 0 ? <span className="text-xs text-text-muted">{drift.out_of_band} outside their band</span> : null
        }
      />
      {data.strategy_config_version_id === null ? (
        <NoStrategy />
      ) : targeted.length === 0 && !cashRow ? (
        <p className="text-sm text-text-muted">The strategy sets no position or cash targets.</p>
      ) : (
        <div className="relative -mx-6 overflow-x-auto px-6">
          <table className="w-full min-w-[30rem] text-sm">
            <caption className="sr-only">Drift from target weights</caption>
            <thead>
              <tr className="border-b border-border bg-surface-muted text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
                <th scope="col" className="px-2 py-2 font-medium">Holding</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">Weight</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">Target</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">Drift</th>
                <th scope="col" className="px-2 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {targeted.map((p) => (
                <tr key={p.ticker} className="border-b border-border-soft last:border-0">
                  <th scope="row" className="px-2 py-2 text-left font-mono text-xs font-normal">
                    <Link href={`/holdings/${encodeURIComponent(p.ticker)}`} className="hover:text-accent-ink hover:underline">
                      {p.ticker}
                    </Link>
                    {p.held ? null : <span className="ml-1 font-sans text-text-muted">(not held)</span>}
                  </th>
                  <td className="num px-2 py-2 text-right whitespace-nowrap">{formatPercent(p.weight)}</td>
                  <td className="num px-2 py-2 text-right whitespace-nowrap text-text-muted">
                    {formatPercent(p.target_weight)}
                    <span className="block text-xs">
                      {formatPercent(p.lower)}–{formatPercent(p.upper)}
                    </span>
                  </td>
                  <td className={cn("num px-2 py-2 text-right whitespace-nowrap", isOutOfBand(p.status) && "font-semibold")}>
                    <span className="inline-flex items-center gap-2">
                      <DriftBar drift={p.drift} />
                      {formatPp(p.drift)}
                    </span>
                  </td>
                  <td className="px-2 py-2">
                    <DriftStatusBadge status={p.status} />
                  </td>
                </tr>
              ))}
              {cashRow ? (
                <tr>
                  <th scope="row" className="px-2 py-2 text-left font-normal">Cash</th>
                  <td className="num px-2 py-2 text-right whitespace-nowrap">{formatPercent(cashRow.weight)}</td>
                  <td className="num px-2 py-2 text-right whitespace-nowrap text-text-muted">
                    {formatPercent(cashRow.target_weight)}
                    <span className="block text-xs">
                      {formatPercent(cashRow.lower)}–{formatPercent(cashRow.upper)}
                    </span>
                  </td>
                  <td className="num px-2 py-2 text-right whitespace-nowrap">
                    <span className="inline-flex items-center gap-2">
                      <DriftBar drift={cashRow.drift} />
                      {formatPp(cashRow.drift)}
                    </span>
                  </td>
                  <td className="px-2 py-2">
                    <DriftStatusBadge status={cashRow.status} />
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      )}
      {untargeted.length > 0 && data.strategy_config_version_id !== null ? (
        <p className="text-xs text-text-muted">
          No target set for {untargeted.map((p) => p.ticker).join(", ")}.{" "}
          <Link href="/holdings" className="text-accent-ink underline-offset-4 hover:underline">
            See all holdings
          </Link>
        </p>
      ) : null}
    </Blueprint>
  );
}
