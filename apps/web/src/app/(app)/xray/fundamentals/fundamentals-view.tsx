"use client";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { coverageLabel, METRIC_ORDER, type FundamentalsData, type MetricData } from "@/lib/xray/xray";

import { Fact, Notes, Pct, TD, TH, TH_LEFT } from "../shared";

const fetchFundamentals = () => getBrowserApiClient().GET("/portfolio/xray/fundamentals");

export function FundamentalsView() {
  const { state } = useApiResource(fetchFundamentals);
  return (
    <ResourceGate state={state} label="fundamentals">
      {(data) => <Fundamentals data={data} />}
    </ResourceGate>
  );
}

function valueText(m: MetricData): string {
  if (m.value === null || m.value === undefined) return "";
  return m.unit === "multiple" ? `${m.value.toFixed(1)}×` : formatPercent(m.value, { decimals: 2 });
}

function MetricRow({ m }: { m: MetricData }) {
  const coverage = m.coverage ?? 0;
  return (
    <tr className="border-t border-border-soft align-top">
      <th scope="row" className="px-3 py-2 text-left font-normal">
        <span className="font-medium">{m.label}</span>
        <span className="mt-0.5 block max-w-md text-[11px] text-text-muted">{m.formula}</span>
      </th>
      <td className="px-3 py-2 text-right">
        {m.value !== null && m.value !== undefined ? (
          <>
            <span className="num text-lg font-semibold">{valueText(m)}</span>
            {m.thin ? <span className="mt-0.5 block text-[11px] font-medium text-warning-signal">covers under half: indicative only</span> : null}
          </>
        ) : (
          <span
            title={m.reason ?? undefined}
            className="inline-block border border-dashed border-border px-1.5 text-[11px] leading-5 text-text-muted"
          >
            No figure<span className="sr-only">: {m.reason}</span>
          </span>
        )}
      </td>
      <td className="px-3 py-2">
        <div className="num text-sm">{coverageLabel(m.coverage)}</div>
        <div aria-hidden="true" className="mt-1 h-1 w-28 bg-border-soft">
          <div className="h-full bg-accent" style={{ width: `${Math.min(100, coverage * 100)}%` }} />
        </div>
      </td>
      <td className="px-3 py-2 text-sm">
        {m.value === null || m.value === undefined ? <p className="text-text-muted">{m.reason}</p> : null}
        {m.excluded_count > 0 ? (
          <details>
            <summary className="cursor-pointer text-text-muted">{m.excluded_count} left out</summary>
            <ul className="mt-1 space-y-0.5 text-xs">
              {m.excluded.map((e) => (
                <li key={`${e.label}-${e.reason}`}>
                  {e.label} <span className="num text-text-muted">{formatPercent(e.weight, { decimals: 1 })}</span>: {e.reason}
                </li>
              ))}
              {m.excluded_count > m.excluded.length ? (
                <li className="text-text-muted">and {m.excluded_count - m.excluded.length} smaller</li>
              ) : null}
            </ul>
          </details>
        ) : m.value !== null && m.value !== undefined ? (
          <span className="text-text-muted">Nothing left out</span>
        ) : null}
      </td>
    </tr>
  );
}

function Fundamentals({ data }: { data: FundamentalsData }) {
  const byId = new Map(data.metrics.map((m) => [m.id, m]));
  const ordered = METRIC_ORDER.map((id) => byId.get(id)).filter((m): m is MetricData => Boolean(m));
  const fees = data.fees;
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker="Weighted across your equity" title="What the companies you own look like on average" />
        <p className="mt-1 max-w-3xl text-sm text-text-muted">
          Each figure covers only the equity the app holds statements and prices for. It says how much that is, what was left out and why. When an
          average would mean nothing, there is no figure.
        </p>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-4">
          <Fact label="Equity identified">
            <Pct value={data.equity_weight} />
          </Fact>
          <Fact label="With statements and prices" note={`${data.scored_issuers} issuers scored`}>
            <Pct value={data.covered_weight} />
          </Fact>
          <Fact label="Financial companies" note="In yield and book value; left out of profitability and leverage">
            <Pct value={data.financials_weight} />
          </Fact>
          <Fact label="Unknown fund weight" note="Outside every figure">
            <Pct value={data.unknown_weight} />
          </Fact>
        </dl>
        <div className="mt-4 overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Weighted portfolio fundamentals with coverage and exclusions</caption>
            <thead>
              <tr className="bg-surface-muted">
                <th scope="col" className={TH_LEFT}>Figure</th>
                <th scope="col" className={TH}>Value</th>
                <th scope="col" className={TH_LEFT}>Coverage</th>
                <th scope="col" className={TH_LEFT}>Left out</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((m) => (
                <MetricRow key={m.id} m={m} />
              ))}
            </tbody>
          </table>
        </div>
      </Blueprint>

      {fees ? (
        <Blueprint className="px-[22px] py-4">
          <PanelHeading kicker="Fee drag" title="What the funds cost each year" />
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-3 md:grid-cols-4">
            <Fact label="Share of portfolio a year">
              <Pct value={fees.annual_drag} decimals={2} />
            </Fact>
            <Fact label="Average ongoing charge" note="Of funds whose charge is known">
              <Pct value={fees.weighted_expense_ratio} decimals={2} />
            </Fact>
            <Fact label="Funds with a known charge">
              <span className="num">{fees.funds_with_ratio}</span>
            </Fact>
            <Fact label="Weight with unknown charge" note={fees.funds_without_ratio.join(", ") || "None"}>
              <Pct value={fees.unpriced_weight} />
            </Fact>
          </dl>
          <table className="mt-3 w-full text-sm">
            <caption className="sr-only">Ongoing charge and yearly drag of each held fund</caption>
            <thead>
              <tr className="border-b border-border text-xs text-text-muted">
                <th scope="col" className={TH_LEFT}>Fund</th>
                <th scope="col" className={TH}>Weight</th>
                <th scope="col" className={TH}>Ongoing charge</th>
                <th scope="col" className={TH}>Drag a year</th>
              </tr>
            </thead>
            <tbody>
              {fees.lines.map((l) => (
                <tr key={l.isin} className="border-b border-border-soft">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">{l.label}</th>
                  <td className={TD}>{formatPercent(l.weight, { decimals: 1 })}</td>
                  <td className={TD}>{l.expense_ratio === null || l.expense_ratio === undefined ? <span className="text-text-muted">unknown</span> : formatPercent(l.expense_ratio, { decimals: 2 })}</td>
                  <td className={TD}>{l.annual_drag === null || l.annual_drag === undefined ? <span className="text-text-muted">not counted</span> : formatPercent(l.annual_drag, { decimals: 3 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-text-muted">{fees.note}</p>
        </Blueprint>
      ) : null}
      <Notes notes={data.notes} />
    </div>
  );
}
