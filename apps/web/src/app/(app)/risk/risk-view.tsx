"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { DrawdownChart } from "@/components/charts/drawdown-chart";
import { LineChart } from "@/components/charts/line-chart";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { usePageSelection } from "@/lib/chat/page-selection-store";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { isRatio, STAT_GROUPS, statLabel, type AnalyticsFigure, type RiskReport } from "@/lib/risk/risk";

import { Notes, Warnings } from "../xray/shared";
import { Absent } from "@/components/company/primitives";
import { Field } from "@/components/risk/common";

const fetchReport = () => getBrowserApiClient().GET("/portfolio/risk/report", { params: { query: {} } });

export function RiskView() {
  const { state } = useApiResource(fetchReport);
  return (
    <ResourceGate state={state} label="the risk report">
      {(report) => <Report report={report} />}
    </ResourceGate>
  );
}

function figureText(key: string, f: AnalyticsFigure): string | null {
  if (f.value === null || f.value === undefined) return null;
  const n = Number(f.value);
  if (!Number.isFinite(n)) return null;
  return isRatio(key) ? n.toFixed(2) : formatPercent(n, { decimals: 1 });
}

function Stat({ id, figure }: { id: string; figure: AnalyticsFigure | undefined }) {
  if (!figure) return null;
  const text = figureText(id, figure);
  const sample = (figure.warnings ?? []).find((w) => /daily observations/.test(w));
  return (
    <div className="grid grid-cols-[1fr_auto] items-baseline gap-x-4 gap-y-0.5 border-b border-border-soft py-2 last:border-b-0">
      <dt className="text-sm">{statLabel(id)}</dt>
      <dd className="num text-right text-base font-semibold">
        {text ?? <Absent reason={(figure.warnings ?? [])[0] ?? "not enough history"} />}
      </dd>
      {text === null ? (
        <p className="col-span-2 text-xs text-text-muted">{(figure.warnings ?? []).join(" ") || "Withheld: not enough history."}</p>
      ) : sample ? (
        <p className="col-span-2 text-xs text-text-muted">{sample}</p>
      ) : null}
    </div>
  );
}

function Report({ report }: { report: RiskReport }) {
  const empty = report.window.observations === 0;
  return (
    <div className="flex flex-col gap-5">
      <Warnings warnings={report.warnings} />
      {empty ? null : (
        <p className="text-sm text-text-muted">
          {report.window.observations} daily returns, {formatDate(report.window.start ?? "")} to {formatDate(report.window.end ?? "")}, in{" "}
          {report.base_currency ?? "the base currency"}.
          {report.benchmark_symbol ? ` Benchmark: ${report.benchmark_symbol}.` : ""} These describe the past sample; they are not forecasts.
        </p>
      )}
      <div className="grid gap-5 lg:grid-cols-2">
        {STAT_GROUPS.map((group) => (
          <Blueprint key={group.title} className="p-4">
            <PanelHeading level={3} title={group.title} />
            <dl className="mt-2">
              {group.keys.map((key) => (
                <Stat key={key} id={key} figure={report.stats[key]} />
              ))}
            </dl>
          </Blueprint>
        ))}
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Drawdown" title="Distance from the peak" />
          <div className="mt-3">
            {report.drawdown_path.length > 1 ? (
              <DrawdownChart
                data={report.drawdown_path.map((p) => ({ date: p.date, value: 1 + p.value }))}
                label="Portfolio drawdown"
                xLabels
              />
            ) : (
              <p className="text-sm text-text-muted">Not enough history to draw this.</p>
            )}
          </div>
        </Blueprint>
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Benchmark" title={`Correlation, rolling ${report.rolling_window} days`} />
          <div className="mt-3">
            {report.rolling_correlation.length > 1 ? (
              <LineChart data={report.rolling_correlation} label="Correlation" area={false} formatValue={(v) => v.toFixed(2)} />
            ) : (
              <p className="text-sm text-text-muted">
                {report.benchmark_symbol
                  ? "Not enough overlapping history for a rolling window."
                  : (report.benchmark_note ?? "No benchmark is set, so there is nothing to correlate with.")}
              </p>
            )}
          </div>
        </Blueprint>
      </div>
      <Diversification report={report} />
      <Matrix report={report} />
      <Notes notes={[report.method]} title="How these are calculated" />
    </div>
  );
}

function Diversification({ report }: { report: RiskReport }) {
  const d = report.diversification;
  const value = d.value === null || d.value === undefined ? null : Number(d.value);
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Diversification" title="How much holding several things helps" />
      {value === null ? (
        <p className="mt-2 text-sm text-text-muted">{(d.warnings ?? [])[0] ?? "Withheld: not enough shared history."}</p>
      ) : (
        <p className="mt-2 max-w-prose text-sm">
          Mixing the holdings at today’s weights cuts volatility by{" "}
          <span className="num font-semibold">{formatPercent(value, { decimals: 0 })}</span> compared with the weighted average of
          holding each one alone. It is based on {formatPercent(Number(report.holdings_covered.numerator) / (Number(report.holdings_covered.denominator) || 1), { decimals: 0 })} of the invested
          value.
        </p>
      )}
    </Blueprint>
  );
}

function cell(v: number | null): React.CSSProperties | undefined {
  if (v === null) return undefined;
  const strength = Math.round(Math.min(1, Math.abs(v)) * 55);
  return { background: `color-mix(in srgb, var(--accent) ${strength}%, transparent)` };
}

function Matrix({ report }: { report: RiskReport }) {
  const m = report.correlation;
  const [focus, setFocus] = useState<string>("");
  usePageSelection(focus.trim() ? `holding ${focus.trim()}` : null);
  // Broker tickers are mixed-case (CSPXl_EQ), so the highlight ignores case.
  const isFocus = (t: string) => focus.trim() !== "" && t.toUpperCase() === focus.trim().toUpperCase();
  if (m.tickers.length < 2) {
    return (
      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Pairs" title="How the largest holdings move together" />
        <p className="mt-2 text-sm text-text-muted">Fewer than two holdings have enough history for pairs.</p>
      </Blueprint>
    );
  }
  return (
    <Blueprint className="p-4">
      <PanelHeading
        level={3}
        kicker="Pairs"
        title="How the largest holdings move together"
        action={
          <Field label="Highlight" className="w-32">
            <Input value={focus} onChange={(e) => setFocus(e.target.value)} placeholder="Ticker" />
          </Field>
        }
      />
      <p className="mt-1 text-sm text-text-muted">
        {m.pairs_measured} of {m.pairs_total} pairs have at least {m.min_observations} shared days
        {m.average === null ? "." : `; their average correlation is ${m.average.toFixed(2)}.`} Darker means more alike; an empty cell
        has too little shared history.
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Correlation between each pair of holdings</caption>
          <thead>
            <tr>
              <th scope="col" className="px-2 py-1"><span className="sr-only">Holding</span></th>
              {m.tickers.map((t) => (
                <th key={t} scope="col" className={`px-2 py-1 text-right font-medium ${isFocus(t) ? "text-accent-ink" : ""}`}>
                  {t}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {m.tickers.map((row, i) => (
              <tr key={row}>
                <th scope="row" className={`px-2 py-1 text-left font-medium ${isFocus(row) ? "text-accent-ink" : ""}`}>
                  {row}
                </th>
                {m.values[i].map((v, j) => (
                  <td
                    key={m.tickers[j]}
                    style={cell(v)}
                    title={`${row} and ${m.tickers[j]}: ${m.observations[i][j]} shared days`}
                    className={`num border border-border-soft px-2 py-1 text-right ${isFocus(row) || isFocus(m.tickers[j]) ? "font-semibold" : ""}`}
                  >
                    {v === null ? "" : v.toFixed(2)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Blueprint>
  );
}
