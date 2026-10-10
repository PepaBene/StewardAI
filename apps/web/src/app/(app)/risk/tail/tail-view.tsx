"use client";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { badMonthSentence, horizonText, yearsText, type TailReport } from "@/lib/risk/tail";

import { FanChart } from "./fan-chart";

const fetchTail = () => getBrowserApiClient().GET("/portfolio/risk/tail");

/** How bad a bad month, and how likely the big fall, on today's weights and the weekly record. */
export function TailView() {
  const { state } = useApiResource(fetchTail);
  return (
    <ResourceGate state={state} label="the bad-month numbers">
      {(r: TailReport) => (r.available ? <Report r={r} /> : <p className="text-sm text-text-muted">{r.reason}</p>)}
    </ResourceGate>
  );
}

function Report({ r }: { r: TailReport }) {
  const lines = r.lines ?? [];
  const odds = r.drawdown ?? [];
  const contributions = r.contributions ?? [];
  const sentence = badMonthSentence(lines, r.base_currency);
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="p-4">
        <PanelHeading level={3} kicker="Bad months" title="What the worst months looked like" />
        {sentence ? <p className="mt-3 max-w-prose text-base font-semibold">{sentence}</p> : null}
        <p className="mt-1 max-w-prose text-sm text-text-muted">
          Based on {r.weeks} weeks of weekly returns of today’s holdings. The loss is the average of the worst months, not the worst one.
        </p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">Loss in a bad period by method</caption>
            <thead>
              <tr className="border-b border-border text-left">
                <th scope="col" className="py-1.5 pr-3 font-medium">Over</th>
                <th scope="col" className="py-1.5 pr-3 font-medium">Worst</th>
                <th scope="col" className="py-1.5 pr-3 font-medium">Method</th>
                <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Loss line</th>
                <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Average beyond it</th>
                <th scope="col" className="num py-1.5 text-right font-medium">In money</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l) => (
                <tr key={`${l.horizon_weeks}-${l.level}-${l.method}`} className="border-b border-border-soft last:border-b-0">
                  <th scope="row" className="py-1.5 pr-3 text-left font-semibold">{horizonText(l.horizon_weeks)}</th>
                  <td className="py-1.5 pr-3">1 in {Math.round(1 / (1 - l.level))}</td>
                  <td className="py-1.5 pr-3">{l.method}</td>
                  <td className="num py-1.5 pr-3 text-right">{formatPercent(l.var)}</td>
                  <td className="num py-1.5 pr-3 text-right">{formatPercent(l.es)}</td>
                  <td className="num py-1.5 text-right">
                    {l.es_value != null ? `${Math.round(l.es_value).toLocaleString("en")} ${r.base_currency ?? ""}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Blueprint>

      {odds.length > 0 ? (
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="The big fall" title="How likely is a fall from a peak?" />
          <p className="mt-3 max-w-prose text-sm text-text-muted">
            {r.paths?.toLocaleString("en")} simulated futures, rebuilt from blocks of your own past weeks, so rough patches stay clustered. The same
            answer every time you ask.
          </p>
          {(() => {
            const longest = odds.reduce((a, b) => (b.years > a.years ? b : a));
            return longest.fan ? <FanChart fan={longest.fan} stepWeeks={longest.fan_step_weeks ?? 4} years={longest.years} /> : null;
          })()}
          <div className="mt-3 overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Chance of a drawdown by horizon</caption>
              <thead>
                <tr className="border-b border-border text-left">
                  <th scope="col" className="py-1.5 pr-3 font-medium">Within</th>
                  <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Chance of a {formatPercent(odds[0].threshold, { decimals: 0 })} fall</th>
                  <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Typical deepest fall</th>
                  <th scope="col" className="num py-1.5 pr-3 text-right font-medium">1 in 20 deepest fall</th>
                  <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Average of the worst 5 %</th>
                  <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Value of 100 at the end (bad / middle / good)</th>
                  <th scope="col" className="num py-1.5 text-right font-medium">Weeks to recover</th>
                </tr>
              </thead>
              <tbody>
                {odds.map((o) => (
                  <tr key={o.years} className="border-b border-border-soft last:border-b-0">
                    <th scope="row" className="py-1.5 pr-3 text-left font-semibold">{yearsText(o.years)}</th>
                    <td className="num py-1.5 pr-3 text-right font-semibold">{formatPercent(o.probability)}</td>
                    <td className="num py-1.5 pr-3 text-right">{formatPercent(o.drawdown_p50)}</td>
                    <td className="num py-1.5 pr-3 text-right">{formatPercent(o.drawdown_p95)}</td>
                    <td className="num py-1.5 pr-3 text-right">{formatPercent(o.conditional_drawdown_at_risk)}</td>
                    <td className="num py-1.5 pr-3 text-right">
                      {Math.round(o.terminal_p05 * 100)} / {Math.round(o.terminal_p50 * 100)} / {Math.round(o.terminal_p95 * 100)}
                    </td>
                    <td className="num py-1.5 text-right">
                      {o.recovery_median_weeks != null ? `${Math.round(o.recovery_median_weeks)}` : "—"}
                      {o.recovered_share != null ? ` (${formatPercent(o.recovered_share, { decimals: 0 })} recover)` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Blueprint>
      ) : null}

      {contributions.length > 0 ? (
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Who carries it" title="Which holdings hurt most in bad weeks" />
          <ul className="mt-3 flex max-w-prose flex-col gap-1 text-sm">
            {contributions.map((c) => (
              <li key={c.ticker} className="flex justify-between gap-3 border-b border-border-soft py-1 last:border-b-0">
                <span>{c.name ?? c.ticker}</span>
                <span className="num">
                  {formatPercent(c.part_of_es, { decimals: 0 })} of the loss, {formatPercent(c.weight, { decimals: 0 })} of the book
                </span>
              </li>
            ))}
          </ul>
        </Blueprint>
      ) : null}

      {r.downside ? (
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Your record" title="How the downside has felt" />
          <dl className="mt-3 grid max-w-xl grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <Stat label="Return a year" value={formatPercent(r.downside.annual_return)} />
            <Stat label="Deepest fall" value={formatPercent(r.downside.max_drawdown)} />
            <Stat label="Downside swing a year" value={formatPercent(r.downside.downside_deviation)} />
            <Stat label="Sortino" value={r.downside.sortino != null ? r.downside.sortino.toFixed(2) : "—"} />
            <Stat label="Calmar" value={r.downside.calmar != null ? r.downside.calmar.toFixed(2) : "—"} />
            <Stat label="Ulcer index" value={formatPercent(r.downside.ulcer_index)} />
          </dl>
        </Blueprint>
      ) : null}

      <ul className="max-w-prose list-disc pl-5 text-xs text-text-muted">
        {(r.warnings ?? []).map((w) => (
          <li key={w}>{w}</li>
        ))}
        {(r.not_modelled ?? []).map((w) => (
          <li key={w}>Not modelled: {w}.</li>
        ))}
      </ul>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="num font-semibold">{value}</dd>
    </div>
  );
}
