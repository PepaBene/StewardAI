"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Field, Problem, problemText } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatMoney, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { parseNumber } from "@/lib/risk/risk";
import {
  pointsChange,
  sharesText,
  stageText,
  type AllocationPlan,
  type ConcentrationReport,
  type OverCap,
  type StartSize,
} from "@/lib/risk/sizing";

const fetchOverCap = () => getBrowserApiClient().GET("/portfolio/risk/over-cap", { params: { query: {} } });

/** What to buy next and how much: the next deposit, the size of a new position, and stocks that have outgrown the cap. */
export function DepositView() {
  return (
    <div className="flex flex-col gap-5">
      <DepositPanel />
      <SizePanel />
      <OverCapPanel />
    </div>
  );
}

function DepositPanel() {
  const [amount, setAmount] = useState("");
  const [plan, setPlan] = useState<AllocationPlan | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    const value = parseNumber(amount);
    if (!value || value <= 0) return setProblem("Enter the amount you are about to deposit, in your base currency.");
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().GET("/portfolio/risk/deposit", { params: { query: { amount: value } } });
    setBusy(false);
    if (!res.response.ok || !res.data) return setProblem(problemText(res.error, "The deposit could not be planned."));
    setPlan(res.data);
  }

  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Next deposit" title="Where should the money go?" />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        Core first while it is below target, then the most under-represented tier, sector or country, then the best idea above your return hurdle,
        else a core fund. Whole shares, and nothing that breaks a rule, a cap or a risk limit.
      </p>
      <form
        className="mt-3 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <Field label="Amount" className="w-48">
          <Input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="20000" />
        </Field>
        <Button type="submit" disabled={busy}>
          {busy ? "Planning…" : "Plan the deposit"}
        </Button>
      </form>
      <div className="mt-3">
        <Problem>{problem}</Problem>
      </div>
      {plan ? <PlanOutcome plan={plan} /> : null}
    </Blueprint>
  );
}

function PlanOutcome({ plan }: { plan: AllocationPlan }) {
  const money = (v: number) => formatMoney(v, plan.base_currency, { decimals: 0 });
  return (
    <div className="mt-4 flex flex-col gap-3 border-t border-border pt-4">
      <p className="text-sm">
        <span className="num text-lg font-semibold">{money(plan.spent)}</span> of {money(plan.amount)} placed,{" "}
        <span className="num">{money(plan.left_over)}</span> left as cash.
        {plan.volatility_before != null && plan.volatility_after != null ? (
          <span className="text-text-muted">
            {" "}
            Forecast volatility {formatPercent(plan.volatility_before)} → {formatPercent(plan.volatility_after)} ({pointsChange(plan.volatility_before, plan.volatility_after)}).
          </span>
        ) : null}
      </p>
      {plan.lines.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <caption className="sr-only">The planned purchases, in the order they were chosen</caption>
            <thead>
              <tr className="border-b border-border text-left">
                <th scope="col" className="py-1.5 pr-3 font-medium">Buy</th>
                <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Shares</th>
                <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Cost</th>
                <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Weight</th>
                <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Volatility</th>
                <th scope="col" className="py-1.5 font-medium">Why</th>
              </tr>
            </thead>
            <tbody>
              {plan.lines.map((l) => (
                <tr key={l.ticker} className="border-b border-border-soft align-top last:border-b-0">
                  <th scope="row" className="py-1.5 pr-3 text-left font-semibold">
                    {l.ticker}
                    {l.name ? <span className="block text-xs font-normal text-text-muted">{l.name}</span> : null}
                  </th>
                  <td className="num py-1.5 pr-3 text-right">{sharesText(l.shares)}</td>
                  <td className="num py-1.5 pr-3 text-right">{money(l.value)}</td>
                  <td className="num py-1.5 pr-3 text-right">
                    {formatPercent(l.weight_before)} → {formatPercent(l.weight_after)}
                  </td>
                  <td className="num py-1.5 pr-3 text-right">
                    {l.volatility_change == null ? "—" : `${l.volatility_change > 0 ? "+" : "−"}${Math.abs(l.volatility_change * 100).toFixed(2)} pts`}
                  </td>
                  <td className="py-1.5 text-text-muted">
                    <span className="font-medium text-text">{stageText(l.stage)}.</span> {l.principle}
                    {l.expected_return != null ? ` Return to its sell target ${formatPercent(l.expected_return, { decimals: 0 })}.` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {(plan.notes ?? []).map((n) => (
        <p key={n} className="text-sm text-text-muted">
          {n}
        </p>
      ))}
      {plan.set_aside.length > 0 ? (
        <details className="text-sm">
          <summary className="cursor-pointer text-text-muted">Left out ({plan.set_aside.length})</summary>
          <ul className="mt-1 space-y-0.5">
            {plan.set_aside.map((s) => (
              <li key={s.ticker}>
                <span className="font-semibold">{s.ticker}</span> <span className="text-text-muted">{s.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <p className="text-xs text-text-muted">{plan.disclaimer}</p>
    </div>
  );
}

function SizePanel() {
  const [ticker, setTicker] = useState("");
  const [size, setSize] = useState<StartSize | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    if (ticker.trim() === "") return setProblem("Enter the ticker of the stock you are considering.");
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().GET("/portfolio/risk/sizing", { params: { query: { ticker: ticker.trim() } } });
    setBusy(false);
    if (!res.response.ok || !res.data) return setProblem(problemText(res.error, "The size could not be worked out."));
    setSize(res.data);
  }

  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="New position" title="How big to start" />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        The smaller of your starting cap for the tier and the largest size that adds no new risk-limit breach. A ceiling, not a target.
      </p>
      <form
        className="mt-3 flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        <Field label="Ticker" className="w-48">
          <Input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="AAPL_US_EQ" />
        </Field>
        <Button type="submit" disabled={busy}>
          {busy ? "Working it out…" : "Size it"}
        </Button>
      </form>
      <div className="mt-3">
        <Problem>{problem}</Problem>
      </div>
      {size ? <SizeOutcome size={size} /> : null}
    </Blueprint>
  );
}

function SizeOutcome({ size }: { size: StartSize }) {
  if (size.status !== "ok" || size.suggested == null) {
    return <p className="mt-4 text-sm text-text-muted">{size.reason ?? "No size could be suggested."}</p>;
  }
  const k = size.kelly;
  return (
    <div className="mt-4 flex flex-col gap-3 border-t border-border pt-4">
      <p className="text-sm">
        Start at <span className="num text-2xl font-semibold">{formatPercent(size.suggested)}</span> of the portfolio
        {size.suggested_value != null ? (
          <span className="num text-text-muted"> ({formatMoney(size.suggested_value, size.base_currency ?? "CZK", { decimals: 0 })})</span>
        ) : null}
        .
      </p>
      <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs text-text-muted">Starting cap for the {size.tier} tier (principle 8b)</dt>
          <dd className="num font-semibold">{formatPercent(size.tier_cap)}</dd>
        </div>
        <div>
          <dt className="text-xs text-text-muted">Largest size inside your risk limits</dt>
          <dd className="num font-semibold">{formatPercent(size.risk_ceiling)}</dd>
        </div>
        <div>
          <dt className="text-xs text-text-muted">Quarter-Kelly upper bound</dt>
          <dd className="num font-semibold">{k?.available && k.bound != null ? formatPercent(k.bound) : "—"}</dd>
        </div>
      </dl>
      <p className="text-sm text-text-muted">
        {size.binding === "risk_limits"
          ? `The risk limits bind first${(size.limits_reached ?? []).length > 0 ? ` (${(size.limits_reached ?? []).join(", ")})` : ""}.`
          : "Your tier cap binds first; the risk limits allow it."}
      </p>
      {k?.available ? (
        <p className="text-xs text-text-muted">
          Kelly inputs: thesis return {formatPercent(k.thesis_return)} a year, pulled toward {formatPercent(k.equilibrium_return)} (the market’s pay for beta{" "}
          {k.beta?.toFixed(2) ?? "1.00"}) with {formatPercent(k.confidence, { decimals: 0 })} weight on the thesis, giving {formatPercent(k.expected_return)}; volatility{" "}
          {formatPercent(k.volatility)}, cash rate {formatPercent(k.risk_free)}. A shown bound, never a target.
        </p>
      ) : (
        <p className="text-xs text-text-muted">No Kelly bound: {k?.reason ?? "no thesis"}.</p>
      )}
      {(size.warnings ?? []).map((w) => (
        <p key={w} className="text-xs text-text-muted">
          {w}
        </p>
      ))}
    </div>
  );
}

function OverCapPanel() {
  const { state } = useApiResource(fetchOverCap);
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Above the cap" title="Stocks that have outgrown it" />
      <div className="mt-3">
        <ResourceGate state={state} label="the cap check">{(report: ConcentrationReport) => <OverCapList report={report} />}</ResourceGate>
      </div>
    </Blueprint>
  );
}

function OverCapList({ report }: { report: ConcentrationReport }) {
  const money = (v: number) => formatMoney(v, report.base_currency ?? "CZK", { decimals: 0 });
  if (report.reason) return <p className="text-sm text-text-muted">{report.reason}</p>;
  if (report.positions.length === 0) {
    return <p className="text-sm text-text-muted">No stock is above the {formatPercent(report.cap, { decimals: 0 })} cap.</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-prose text-sm text-text-muted">
        Principle 4f: while the thesis still projects growth, hold, add nothing and let deposits dilute it; once the growth is used up or the thesis
        is weakened, trim to {formatPercent(report.trim_to, { decimals: 0 })}. Deposits assumed: {money(report.monthly_deposit)} a month ({report.deposit_source}).
      </p>
      <ul className="divide-y divide-border-soft">
        {report.positions.map((p: OverCap) => (
          <li key={p.ticker} className="py-3 first:pt-0 last:pb-0">
            <p className="text-sm">
              <span className="font-semibold">{p.ticker}</span> {p.name ? <span className="text-text-muted">{p.name} </span> : null}
              <span className="num">{formatPercent(p.weight)}</span> <span className="text-text-muted">of the portfolio, {money(p.excess_value)} above the cap</span>
            </p>
            <p className="mt-0.5 max-w-prose text-sm text-text-muted">{p.advice}</p>
            {(p.thesis_gaps ?? []).length > 0 ? (
              <p className="mt-0.5 text-xs text-text-muted">The thesis is missing: {(p.thesis_gaps ?? []).join(", ")}.</p>
            ) : null}
          </li>
        ))}
      </ul>
      {(report.exempt ?? []).length > 0 ? <p className="text-xs text-text-muted">Funds above the cap are exempt: {(report.exempt ?? []).join(", ")}.</p> : null}
    </div>
  );
}
