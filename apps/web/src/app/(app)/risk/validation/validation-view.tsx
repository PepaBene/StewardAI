"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { SELECT } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { StatusDot } from "@/components/ui/status-dot";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  basisPointsText,
  confidenceText,
  pValueText,
  signedPercent,
  testOutcome,
  type ClosedThesis,
  type Evaluation,
  type ModelValidation,
  type PeriodOut,
  type ProcessReport,
} from "@/lib/risk/validation";

const fetchValidation = () => getBrowserApiClient().GET("/portfolio/risk/validation");
const fetchProcess = () => getBrowserApiClient().GET("/journal/process");

/** Is the risk model right, and is the process adding value? The checks the other risk pages lean on. */
export function ValidationView() {
  return (
    <div className="flex flex-col gap-5">
      <ModelPanel />
      <EvaluationPanel />
      <ProcessPanel />
    </div>
  );
}

function ModelPanel() {
  const { state } = useApiResource(fetchValidation);
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="The risk model" title="Did its forecasts match what happened?" />
      <div className="mt-3">
        <ResourceGate state={state} label="the model check">{(v: ModelValidation) => <ModelOutcome v={v} />}</ResourceGate>
      </div>
    </Blueprint>
  );
}

function ModelOutcome({ v }: { v: ModelValidation }) {
  if (!v.available) return <p className="text-sm text-text-muted">{v.reason}</p>;
  const cal = v.calibration;
  return (
    <div className="flex flex-col gap-4">
      <p className="flex items-start gap-2 text-sm">
        <StatusDot tone={v.confidence === "ok" ? "neutral" : "warning"} className="mt-1.5" />
        <span>
          <span className="font-semibold">{confidenceText(v.confidence)}.</span> {v.verdict}
        </span>
      </p>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Value-at-risk back-test results</caption>
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="py-1.5 pr-3 font-medium">Worst-case line</th>
              <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Weeks beyond it</th>
              <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Expected</th>
              <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Count test</th>
              <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Clustering test</th>
              <th scope="col" className="py-1.5 font-medium">Result</th>
            </tr>
          </thead>
          <tbody>
            {(v.backtests ?? []).map((b) => (
              <tr key={b.confidence} className="border-b border-border-soft last:border-b-0">
                <th scope="row" className="py-1.5 pr-3 text-left font-semibold">
                  Loses more than the {formatPercent(b.confidence, { decimals: 0 })} line
                </th>
                <td className="num py-1.5 pr-3 text-right">{b.hits} of {b.weeks}</td>
                <td className="num py-1.5 pr-3 text-right">{b.expected_hits.toFixed(1)}</td>
                <td className="num py-1.5 pr-3 text-right">p {pValueText(b.kupiec_p)}</td>
                <td className="num py-1.5 pr-3 text-right">p {pValueText(b.independence_p)}</td>
                <td className="py-1.5">{testOutcome(b.passed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cal && cal.realised_over_forecast != null ? (
        <p className="max-w-prose text-sm text-text-muted">
          Realised moves were <span className="num">{cal.realised_over_forecast.toFixed(2)}</span> times the forecast (1.00 is perfect; the
          uncertainty is about ±{(2 * (cal.standard_error ?? 0)).toFixed(2)}). Average forecast volatility{" "}
          {formatPercent(cal.forecast_volatility)} a year, realised {formatPercent(cal.realised_volatility)} over the same {cal.weeks} weeks.
        </p>
      ) : null}
      <ul className="max-w-prose list-disc pl-5 text-xs text-text-muted">
        {(v.warnings ?? []).map((w) => (
          <li key={w}>{w}</li>
        ))}
        <li>{v.method}</li>
        <li>It tests the method on today’s holdings, not on the trades you actually made.</li>
      </ul>
    </div>
  );
}

function EvaluationPanel() {
  const [dimension, setDimension] = useState("sector");
  // A fetcher per dimension; the key remounts the loader so a change refetches.
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Your record" title="Is your process beating the benchmark, and is it skill?" />
      <label className="mt-3 flex max-w-xs flex-col gap-1 text-sm">
        <span className="font-medium">Split the return by</span>
        <select className={SELECT} value={dimension} onChange={(e) => setDimension(e.target.value)}>
          <option value="sector">Sector</option>
          <option value="risk">Risk tier</option>
          <option value="geography">Geography</option>
          <option value="currency">Currency</option>
        </select>
      </label>
      <EvaluationBody key={dimension} dimension={dimension} />
    </Blueprint>
  );
}

function EvaluationBody({ dimension }: { dimension: string }) {
  const { state } = useApiResource(() =>
    getBrowserApiClient().GET("/portfolio/performance/evaluation", { params: { query: { dimension } } }),
  );
  return (
    <div className="mt-3">
      <ResourceGate state={state} label="the evaluation">{(e: Evaluation) => <EvaluationOutcome e={e} />}</ResourceGate>
    </div>
  );
}

function EvaluationOutcome({ e }: { e: Evaluation }) {
  if (!e.available) return <p className="text-sm text-text-muted">{e.reason}</p>;
  const skill = e.skill;
  const alpha = e.factor_alpha;
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        {skill ? (
          <p className="max-w-prose text-sm">
            <span className="font-semibold">Skill or luck?</span> {skill.verdict}
            {skill.portfolio_sharpe != null && skill.benchmark_sharpe != null ? (
              <span className="text-text-muted">
                {" "}
                Sharpe ratio <span className="num">{skill.portfolio_sharpe.toFixed(2)}</span> against{" "}
                <span className="num">{skill.benchmark_sharpe.toFixed(2)}</span> for {e.benchmark_label}.
              </span>
            ) : null}
          </p>
        ) : null}
        {alpha ? (
          <p className="max-w-prose text-sm">
            <span className="font-semibold">After the factors.</span> {alpha.verdict}
          </p>
        ) : null}
      </div>
      {e.since_inception ? <PeriodTable periods={[e.since_inception, ...(e.periods ?? []).slice().reverse()]} benchmark={e.benchmark_label} /> : null}
      <ul className="max-w-prose list-disc pl-5 text-xs text-text-muted">
        {(e.notes ?? []).map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </div>
  );
}

function PeriodTable({ periods, benchmark }: { periods: PeriodOut[]; benchmark?: string | null }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">Return against {benchmark ?? "the benchmark"} and what explains the difference</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Period</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">You</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">{benchmark ?? "Benchmark"}</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Difference</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Biggest contributors to the difference</th>
            <th scope="col" className="num py-1.5 text-right font-medium">Adds up to</th>
          </tr>
        </thead>
        <tbody>
          {periods.map((p) => {
            const top = p.segments
              .slice()
              .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution))
              .slice(0, 2);
            return (
              <tr key={p.label} className="border-b border-border-soft align-top last:border-b-0">
                <th scope="row" className="py-1.5 pr-3 text-left font-semibold capitalize">{p.label}</th>
                <td className="num py-1.5 pr-3 text-right">{signedPercent(p.portfolio_return)}</td>
                <td className="num py-1.5 pr-3 text-right">{signedPercent(p.benchmark_return)}</td>
                <td className="num py-1.5 pr-3 text-right font-semibold">{signedPercent(p.excess_return)}</td>
                <td className="py-1.5 pr-3 text-text-muted">
                  {top.map((s) => `${s.name} ${signedPercent(s.contribution)}`).join(", ")}
                  {p.opened_in_period !== 0 ? `${top.length ? ", " : ""}new positions ${signedPercent(p.opened_in_period)}` : ""}
                </td>
                <td className="num py-1.5 text-right text-text-muted">{basisPointsText(p.reconciliation_error_bp)} off</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ProcessPanel() {
  const { state } = useApiResource(fetchProcess);
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Your decisions" title="Did the theses you closed beat the benchmark?" />
      <div className="mt-3">
        <ResourceGate state={state} label="the closed theses">{(r: ProcessReport) => <ProcessOutcome r={r} />}</ResourceGate>
      </div>
    </Blueprint>
  );
}

function ProcessOutcome({ r }: { r: ProcessReport }) {
  if (!r.available) return <p className="text-sm text-text-muted">{r.reason}</p>;
  const trig = r.triggers;
  return (
    <div className="flex flex-col gap-4">
      {(r.by_tier ?? []).length > 0 ? (
        <ul className="grid gap-x-8 gap-y-1 sm:grid-cols-2">
          {(r.by_tier ?? []).map((t) => (
            <li key={t.tier} className="text-sm">
              <span className="font-semibold capitalize">{t.tier}</span>{" "}
              {t.enough && t.hit_rate != null ? (
                <span>
                  beat the benchmark in <span className="num">{formatPercent(t.hit_rate, { decimals: 0 })}</span> of {t.with_return} closed theses
                </span>
              ) : (
                <span className="text-text-muted">
                  {t.closed} closed, too few to say (at least 5 are needed)
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {trig ? (
        <p className="max-w-prose text-sm">
          {trig.enough && trig.share != null
            ? `Of ${trig.losing_theses} losing theses, ${formatPercent(trig.share, { decimals: 0 })} had a risk finding open on the stock before you sold.`
            : `${trig.losing_theses} losing ${trig.losing_theses === 1 ? "thesis" : "theses"} so far; too few to say whether the risk findings came first.`}
        </p>
      ) : null}
      <ThesisTable theses={r.theses ?? []} />
      <ul className="max-w-prose list-disc pl-5 text-xs text-text-muted">
        {(r.notes ?? []).map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </div>
  );
}

function ThesisTable({ theses }: { theses: ClosedThesis[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">Closed theses with their price return against the benchmark</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Stock</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Held</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Return</th>
            <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Benchmark</th>
            <th scope="col" className="py-1.5 font-medium">Note</th>
          </tr>
        </thead>
        <tbody>
          {theses.map((t) => (
            <tr key={`${t.ticker}-${t.opened_on}`} className="border-b border-border-soft last:border-b-0">
              <th scope="row" className="py-1.5 pr-3 text-left font-semibold">{t.ticker}</th>
              <td className="py-1.5 pr-3 text-text-muted">{t.opened_on} to {t.closed_on}</td>
              <td className="num py-1.5 pr-3 text-right">{signedPercent(t.price_return)}</td>
              <td className="num py-1.5 pr-3 text-right">{signedPercent(t.benchmark_return)}</td>
              <td className="py-1.5 text-text-muted">
                {t.reason ?? (t.trigger_before_close === true ? "A risk finding came before the sale" : t.trigger_before_close === false ? "No risk finding before the sale" : "")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
