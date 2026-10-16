"use client";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { components } from "@/lib/api/schema";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

/*
 * Insight coverage (insight I0.1, I0.2): per holding and per planned insight feature, whether the
 * stored data supports it (`ready`), only partly (`partial`) or not at all, with the reason on
 * every cell; and the dated access verdict of each candidate dividend and consensus endpoint.
 */

type Coverage = components["schemas"]["InsightCoverage"];
type Verdict = components["schemas"]["AccessVerdict"];
type State = Coverage["holdings"][number]["cells"][number]["state"];
type ClockData = components["schemas"]["ClockRetrospective"];

const fetchCoverage = () => getBrowserApiClient().GET("/quality/insight-coverage");
const fetchAccess = () => getBrowserApiClient().GET("/sources/insight-access");
const fetchClocks = () => getBrowserApiClient().GET("/quality/insight-clocks");

const CELL: Record<State, { text: string; className: string }> = {
  ready: { text: "Ready", className: "bg-gain-subtle text-text" },
  partial: { text: "Partial", className: "bg-surface-muted text-text" },
  unavailable: { text: "None", className: "bg-loss-subtle text-text-muted" },
};

const SHORT: Record<string, string> = {
  dividend_history: "Dividend history",
  dividend_dates: "Pay dates",
  dividend_matching: "Received matched",
  statement_inputs: "Filed inputs",
  estimates: "Estimates",
  consensus: "Price target",
  peers: "Peer medians",
  fair_value: "Fair value",
  f_dividend_income: "Dividend income",
  f_checks: "Checks",
  f_fair_value_history: "Fair-value history",
  f_multiple_path: "Multiple path",
  f_ownership: "Ownership",
  f_pay_tenure: "Pay and tenure",
  f_earnings_summary: "Earnings summary",
};

const GROUPS = [
  { kind: "input", title: "Inputs" },
  { kind: "feature", title: "Features" },
] as const;

function Matrix({ data }: { data: Coverage }) {
  const areas = data.areas.map((a) => a.area);
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto border border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">Holdings covered per insight input and per feature</caption>
          <thead>
            <tr className="bg-surface-muted text-left text-xs text-text-muted">
              <th scope="col" className="px-3 py-1.5 font-medium">Area</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Ready</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">Partial</th>
              <th scope="col" className="px-2 py-1.5 text-right font-medium">None</th>
            </tr>
          </thead>
          {GROUPS.map((g) => {
            const rows = data.areas.filter((a) => (a.kind ?? "input") === g.kind);
            if (rows.length === 0) return null;
            return (
              <tbody key={g.kind}>
                <tr className="border-t border-border">
                  <th scope="rowgroup" colSpan={4} className="px-3 pt-2.5 pb-1 text-left text-xs font-medium text-text-muted">
                    {g.title}
                  </th>
                </tr>
                {rows.map((a) => (
                  <tr key={a.area} className="border-t border-border-soft">
                    <th scope="row" className="px-3 py-1.5 text-left font-normal">{a.label}</th>
                    <td className="num px-2 py-1.5 text-right">{a.ready}</td>
                    <td className="num px-2 py-1.5 text-right">{a.partial}</td>
                    <td className="num px-2 py-1.5 text-right">{a.unavailable}</td>
                  </tr>
                ))}
              </tbody>
            );
          })}
        </table>
      </div>
      <details>
        <summary className="cursor-pointer text-sm text-accent-ink">Every holding, with the reason in each cell</summary>
        <div className="mt-2 max-h-[480px] overflow-auto border border-border">
          <table className="w-full text-xs">
            <caption className="sr-only">Insight coverage per holding; each cell gives the state and the reason</caption>
            <thead className="sticky top-0">
              <tr className="bg-surface-muted text-left text-text-muted">
                <th scope="col" className="px-2 py-1.5 font-medium">Holding</th>
                {areas.map((a) => (
                  <th key={a} scope="col" className="px-1.5 py-1.5 font-medium whitespace-nowrap">
                    {SHORT[a] ?? a}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.holdings.map((h) => (
                <tr key={h.ticker} className="border-t border-border-soft">
                  <th scope="row" className="px-2 py-1 text-left font-normal whitespace-nowrap">
                    {h.name ?? h.ticker}
                  </th>
                  {areas.map((a) => {
                    const c = h.cells.find((x) => x.area === a);
                    if (!c) return <td key={a} className="px-1.5 py-1 text-text-muted">—</td>;
                    return (
                      <td key={a} className="px-1 py-1">
                        <span title={c.reason} className={cn("block px-1.5 py-0.5 text-center", CELL[c.state].className)}>
                          {CELL[c.state].text}
                          <span className="sr-only">: {c.reason}</span>
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <p className="text-xs text-text-muted">
        Measured {formatDate(data.as_of)} from stored rows only; method {data.method_version}. Hover a cell for its reason.
      </p>
    </div>
  );
}

function Access({ rows }: { rows: Verdict[] }) {
  if (rows.length === 0) return <p className="text-sm text-text-muted">No candidate endpoint has been tested yet. The insight access probe job records one verdict per endpoint.</p>;
  return (
    <ul className="divide-y divide-border-soft text-sm">
      {rows.map((r) => (
        <li key={`${r.provider}.${r.endpoint}`} className="py-1.5">
          <span className="font-medium">
            {r.provider} · {r.endpoint}
          </span>{" "}
          <span className={cn("ml-1 px-1.5 text-xs", r.state === "available" ? "bg-gain-subtle" : r.state === "paid_only" ? "bg-loss-subtle" : "bg-surface-muted")}>{r.state.replace("_", " ")}</span>
          <span className="block text-xs text-text-muted">
            {r.tested_at ? `tested ${formatDate(r.tested_at)}` : "never tested"}
            {r.status_code ? ` · HTTP ${r.status_code}` : ""}
            {r.detail ? ` · ${r.detail}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

const pct = (v: number | null | undefined) => formatPercent(v, { decimals: 0 });

function Clocks({ data }: { data: ClockData }) {
  return (
    <div className="space-y-3">
      <ul className="grid gap-2 text-sm sm:grid-cols-3">
        {data.clocks.map((c) => (
          <li key={c.clock} className="border border-border-soft px-3 py-2">
            <span className="block text-xs text-text-muted">{c.label}</span>
            {c.started ? `started ${formatDate(c.started)}` : "not started"}
          </li>
        ))}
      </ul>
      <div className="overflow-x-auto border border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">What each insight clock showed at 3, 6 and 12 months</caption>
          <thead>
            <tr className="bg-surface-muted text-left text-xs text-text-muted">
              <th scope="col" className="px-3 py-1.5 font-medium">Checkpoint</th>
              <th scope="col" className="px-2 py-1.5 font-medium">Dividend forecast paid</th>
              <th scope="col" className="px-2 py-1.5 font-medium">Price target reached</th>
              <th scope="col" className="px-2 py-1.5 font-medium">Usable fair-value history</th>
            </tr>
          </thead>
          <tbody>
            {data.checkpoints.map((c) => (
              <tr key={c.horizon_months} className="border-t border-border-soft align-top">
                <th scope="row" className="px-3 py-1.5 text-left font-normal whitespace-nowrap">
                  {c.horizon_months} months
                  <span className="block text-xs text-text-muted">
                    {c.reached ? "reached" : c.available_from ? `from ${formatDate(c.available_from)}` : "no clock started"}
                  </span>
                </th>
                <td className="px-2 py-1.5">
                  {c.dividend_hit_rate == null ? "—" : `${pct(c.dividend_hit_rate)} of ${c.dividend_rows}`}
                  {c.dividend_note ? <span className="block text-xs text-text-muted">{c.dividend_note}</span> : null}
                </td>
                <td className="px-2 py-1.5">
                  {c.consensus_hit_rate == null ? "—" : `${pct(c.consensus_hit_rate)} of ${c.consensus_judged}`}
                  {c.consensus_note ? <span className="block text-xs text-text-muted">{c.consensus_note}</span> : null}
                </td>
                <td className="px-2 py-1.5">
                  {c.fair_value_share == null ? "—" : `${c.fair_value_usable} of ${c.fair_value_holdings} holdings`}
                  {c.fair_value_note ? <span className="block text-xs text-text-muted">{c.fair_value_note}</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-text-muted">
        Each clock starts on its first stored snapshot; a checkpoint not yet reached shows the figures so far. Method {data.method_version}.
      </p>
    </div>
  );
}

export function InsightCoveragePanel() {
  const { state: coverage } = useApiResource(fetchCoverage);
  const { state: access } = useApiResource(fetchAccess);
  const { state: clocks } = useApiResource(fetchClocks);
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Blueprint className="p-4 lg:col-span-2">
        <PanelHeading level={3} kicker="Forecasts and targets judged after the fact" title="Insight clocks" />
        <div className="mt-3">
          <ResourceGate state={clocks} label="the insight clocks">
            {(d) => <Clocks data={d} />}
          </ResourceGate>
        </div>
      </Blueprint>
      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="What dividends, checks and targets can claim" title="Insight coverage" />
        <div className="mt-3">
          <ResourceGate state={coverage} label="the insight coverage">
            {(d) => <Matrix data={d} />}
          </ResourceGate>
        </div>
      </Blueprint>
      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Second dividend source and price targets" title="Endpoint access verdicts" />
        <div className="mt-3">
          <ResourceGate state={access} label="the access verdicts">
            {(rows) => <Access rows={rows} />}
          </ResourceGate>
        </div>
      </Blueprint>
    </div>
  );
}
