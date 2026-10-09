"use client";

import { Absent, ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  barWidth,
  budgetStatus,
  coverageSentence,
  riskMultiple,
  sharedMax,
  type ExAnteRisk,
  type GroupRisk,
  type PositionExAnte,
  type TierCheck,
} from "@/lib/risk/ex-ante";
import { cn } from "@/lib/utils";

import { Notes, Warnings } from "../../xray/shared";

const fetchExAnte = () => getBrowserApiClient().GET("/portfolio/risk/ex-ante");

const pct = (v: number | null | undefined, decimals = 1) => formatPercent(v, { decimals });

export function ExAnteView() {
  const { state } = useApiResource(fetchExAnte);
  return (
    <ResourceGate state={state} label="today’s risk">
      {(report) => <Report report={report} />}
    </ResourceGate>
  );
}

function Report({ report }: { report: ExAnteRisk }) {
  if (report.volatility_forecast === null || report.volatility_forecast === undefined) {
    return (
      <div className="flex flex-col gap-5">
        <Warnings warnings={report.warnings} />
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Today’s risk" title="No forecast yet" />
          <p className="mt-2 max-w-prose text-sm text-text-muted">
            {report.reason ?? "The model has nothing to work from."} Daily prices are collected by the prices job; once a holding has
            half a year of weekly closes it is included.
          </p>
        </Blueprint>
      </div>
    );
  }
  const coverage = coverageSentence(report.coverage);
  return (
    <div className="flex flex-col gap-5">
      <Warnings warnings={report.warnings.filter((w) => w.includes("understates"))} />
      <VolatilityStrip report={report} />
      <Ledger report={report} />
      <div className="grid gap-5 lg:grid-cols-2">
        <Groups title="By risk tier" kicker="Tier" groups={report.by_tier} budgets={report.limits.tier_risk_budgets ?? undefined} />
        <Groups title="By sector" kicker="Sector" groups={report.by_sector} />
        <Groups title="By country" kicker="Country" groups={report.by_country} />
        <Groups title="By currency" kicker="Currency" groups={report.by_currency} />
      </div>
      <Diversity report={report} />
      <TierChecks checks={report.tier_checks} />
      {coverage ? <p className="text-sm text-text-muted">{coverage}</p> : null}
      <Notes
        title="How this is calculated"
        notes={[
          report.method,
          `Window ${formatDate(report.window_start ?? "")} to ${formatDate(report.window_end ?? "")}, ${report.weeks} weekly returns. Method ${report.method_version}.`,
          ...report.warnings.filter((w) => !w.includes("understates")),
        ]}
      />
    </div>
  );
}

/** Forecast against realized against the limit: three bars on one scale, the limit as a tick. */
function VolatilityStrip({ report }: { report: ExAnteRisk }) {
  const limit = report.limits.max_volatility ?? null;
  const rows = [
    { id: "ewma", label: "Forecast, recent weeks weigh most", value: report.volatility_ewma, chosen: report.forecast_model === "ewma" },
    {
      id: "lw",
      label: "Forecast, three-year average",
      value: report.volatility_ledoit_wolf,
      chosen: report.forecast_model === "ledoit_wolf",
    },
    { id: "real", label: "Realized, last year", value: report.realized_volatility, chosen: false },
  ];
  const max = sharedMax([...rows.map((r) => r.value ?? 0), limit ?? 0]) * 1.15;
  const over = limit !== null && (report.volatility_forecast ?? 0) > limit;
  return (
    <Blueprint className="p-4">
      <PanelHeading
        level={3}
        kicker="Today’s risk"
        title={
          <>
            <span className="num">{pct(report.volatility_forecast)}</span> a year, forecast from today’s holdings
          </>
        }
      />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        {limit === null
          ? "No volatility limit is set."
          : over
            ? `Above your ${pct(limit, 0)} limit. That asks for a thesis review, not a sale.`
            : `Inside your ${pct(limit, 0)} limit.`}{" "}
        The forecast shown is the higher of the two models. Cash counts as no volatility.
      </p>
      <ul className="mt-4 flex flex-col gap-3">
        {rows.map((r) => (
          <li key={r.id} className="grid grid-cols-[minmax(0,13rem)_1fr_4rem] items-center gap-3 text-sm max-sm:grid-cols-[1fr_4rem]">
            <span className={cn(r.chosen && "font-semibold")}>{r.label}</span>
            <div aria-hidden="true" className="relative h-3 bg-border-soft max-sm:col-span-2 max-sm:row-start-2">
              {r.value !== null && r.value !== undefined ? (
                <div className={cn("h-full", r.chosen ? "bg-accent" : "bg-text-muted/60")} style={{ width: `${barWidth(r.value, max)}%` }} />
              ) : null}
              {limit !== null ? (
                <div className="absolute -top-1 -bottom-1 w-px bg-warning-signal" style={{ left: `${barWidth(limit, max)}%` }} />
              ) : null}
            </div>
            <span className="num text-right font-semibold">
              {r.value === null || r.value === undefined ? <Absent reason="not enough history for a year of returns" /> : pct(r.value)}
            </span>
          </li>
        ))}
      </ul>
      {limit !== null ? (
        <p className="mt-2 text-xs text-text-muted">
          <span aria-hidden="true" className="mr-1 inline-block h-3 w-px translate-y-0.5 bg-warning-signal" />
          Limit {pct(limit, 0)}
        </p>
      ) : null}
    </Blueprint>
  );
}

/** Each position's share of the weight beside its share of the risk. */
function Ledger({ report }: { report: ExAnteRisk }) {
  const invested = report.coverage?.invested_weight ?? 0;
  const scale = sharedMax(report.positions.flatMap((p) => [p.weight, p.risk_share ?? 0]));
  return (
    <Blueprint className="p-4">
      <PanelHeading
        level={3}
        kicker="Where the risk comes from"
        title="Size against share of risk"
      />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        The grey bar is how much of the portfolio a position is. The coloured bar is how much of the portfolio’s risk it brings, counting
        how it moves with everything else. The risk shares add up to 100%.
      </p>
      <ul className="mt-3">
        {report.positions.map((p) => (
          <LedgerRow key={p.ticker} p={p} scale={scale} invested={invested} />
        ))}
      </ul>
    </Blueprint>
  );
}

function LedgerRow({ p, scale, invested }: { p: PositionExAnte; scale: number; invested: number }) {
  const multiple = p.risk_share === null || p.risk_share === undefined ? null : riskMultiple(p.weight / (invested || 1), p.risk_share);
  const heavy = multiple !== null && multiple >= 1.5;
  return (
    <li className="grid grid-cols-[minmax(0,11rem)_1fr_9rem] items-center gap-x-4 gap-y-1 border-b border-border-soft py-2.5 last:border-b-0 max-md:grid-cols-[1fr_auto]">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold">{p.ticker}</div>
        <div className="truncate text-xs text-text-muted">
          {p.tier}
          {p.name ? ` · ${p.name}` : ""}
        </div>
      </div>
      <div aria-hidden="true" className="flex flex-col gap-1 max-md:col-span-2 max-md:row-start-2">
        <div className="h-1.5 bg-border-soft">
          <div className="h-full bg-text-muted/60" style={{ width: `${barWidth(p.weight, scale)}%` }} />
        </div>
        <div className="h-2.5 bg-border-soft">
          {p.risk_share !== null && p.risk_share !== undefined ? (
            <div className={cn("h-full", heavy ? "bg-warning-signal" : "bg-accent")} style={{ width: `${barWidth(p.risk_share, scale)}%` }} />
          ) : null}
        </div>
      </div>
      <dl className="num grid grid-cols-[auto_auto] justify-end gap-x-3 text-right text-sm">
        <dt className="text-text-muted">size</dt>
        <dd>{pct(p.weight)}</dd>
        <dt className="text-text-muted">risk</dt>
        <dd className="font-semibold">{p.risk_share === null || p.risk_share === undefined ? "left out" : pct(p.risk_share)}</dd>
      </dl>
      <ProxyNote p={p} />
    </li>
  );
}

function ProxyNote({ p }: { p: PositionExAnte }) {
  if (p.status === "excluded") {
    return <p className="col-span-full text-xs text-warning-signal">Left out of the model: {p.reason}</p>;
  }
  if (p.status === "proxied" && p.proxy) {
    return (
      <p className="col-span-full text-xs text-text-muted">
        Only {p.real_weeks} weeks of its own prices. The earlier {p.proxy.backfilled_weeks} weeks are estimated from {p.proxy.label}{" "}
        (beta {p.proxy.beta.toFixed(2)}, R² {p.proxy.r_squared.toFixed(2)}, fitted {formatDate(p.proxy.fit_start)} to{" "}
        {formatDate(p.proxy.fit_end)}).
      </p>
    );
  }
  return null;
}

function Groups({
  title,
  kicker,
  groups,
  budgets,
}: {
  title: string;
  kicker: string;
  groups: GroupRisk[];
  budgets?: Record<string, number>;
}) {
  const scale = sharedMax(groups.flatMap((g) => [g.weight, g.risk_share, ...(budgets && budgets[g.label] !== undefined ? [budgets[g.label]] : [])]));
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker={kicker} title={title} />
      {groups.length === 0 ? (
        <p className="mt-2 text-sm text-text-muted">Nothing to group.</p>
      ) : (
        <ul className="mt-2">
          {groups.map((g) => {
            const budget = budgets?.[g.label];
            const status = budgetStatus(g.risk_share, budget);
            return (
              <li key={g.label} className="border-b border-border-soft py-2 last:border-b-0">
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate font-medium">{g.label}</span>
                  <span className="num shrink-0 text-right">
                    <span className="text-text-muted">{pct(g.weight, 0)} of value, </span>
                    <span className="font-semibold">{pct(g.risk_share, 0)} of risk</span>
                  </span>
                </div>
                <div aria-hidden="true" className="relative mt-1 flex flex-col gap-1">
                  <div className="h-1.5 bg-border-soft">
                    <div className="h-full bg-text-muted/60" style={{ width: `${barWidth(g.weight, scale)}%` }} />
                  </div>
                  <div className="h-2.5 bg-border-soft">
                    <div className={cn("h-full", status === "over" ? "bg-warning-signal" : "bg-accent")} style={{ width: `${barWidth(g.risk_share, scale)}%` }} />
                  </div>
                  {budget !== undefined ? (
                    <div className="absolute -top-0.5 -bottom-0.5 w-px bg-text" style={{ left: `${barWidth(budget, scale)}%` }} />
                  ) : null}
                </div>
                {budget !== undefined ? (
                  <p className={cn("mt-1 text-xs", status === "over" ? "text-warning-signal" : "text-text-muted")}>
                    Risk budget {pct(budget, 0)}
                    {status === "over" ? `: over by ${pct(g.risk_share - budget, 0)} points, which asks for a review` : ""}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Blueprint>
  );
}

function Diversity({ report }: { report: ExAnteRisk }) {
  const bets = report.effective_bets;
  const floor = report.limits.min_effective_bets ?? null;
  const ratio = report.diversification_ratio;
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Diversification" title="How many separate bets you hold" />
      <dl className="mt-2 grid gap-x-8 gap-y-1 sm:grid-cols-2">
        <div className="border-b border-border-soft py-2">
          <dt className="text-sm">Independent bets</dt>
          <dd className="num text-2xl font-semibold">{bets === null || bets === undefined ? "—" : bets.toFixed(1)}</dd>
          <p className="mt-1 max-w-prose text-xs text-text-muted">
            Many names can still be one bet if they move together.
            {floor !== null ? ` Your floor is ${floor.toFixed(1)}.` : ""}
          </p>
        </div>
        <div className="border-b border-border-soft py-2">
          <dt className="text-sm">Diversification ratio</dt>
          <dd className="num text-2xl font-semibold">{ratio === null || ratio === undefined ? "—" : ratio.toFixed(2)}</dd>
          <p className="mt-1 max-w-prose text-xs text-text-muted">
            The volatility of holding each name alone, weighted, divided by the volatility of the mix. 1.00 means no help from mixing.
          </p>
        </div>
      </dl>
    </Blueprint>
  );
}

function TierChecks({ checks }: { checks: TierCheck[] }) {
  if (checks.length === 0) return null;
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="Tier criteria" title="Does each holding still fit its tier?" />
      <div className="mt-2 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Volatility of each tiered holding against its tier criterion</caption>
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="py-1.5 pr-3 font-medium">Holding</th>
              <th scope="col" className="py-1.5 pr-3 font-medium">Tier</th>
              <th scope="col" className="num py-1.5 pr-3 text-right font-medium">Volatility</th>
              <th scope="col" className="py-1.5 font-medium">Criterion</th>
            </tr>
          </thead>
          <tbody>
            {checks.map((c) => (
              <tr key={`${c.ticker}-${c.rule}`} className="border-b border-border-soft last:border-b-0">
                <th scope="row" className="py-1.5 pr-3 text-left font-semibold">{c.ticker}</th>
                <td className="py-1.5 pr-3">{c.tier}</td>
                <td className="num py-1.5 pr-3 text-right">{pct(c.volatility)}</td>
                <td className={cn("py-1.5", c.breached && "text-warning-signal")}>
                  {c.rule === "speculative_above" ? `Speculative above ${pct(c.threshold, 0)}` : `Core up to ${pct(c.threshold, 0)}`}
                  {c.breached ? ": review its tier" : ": fits"}
                  <span className="text-text-muted"> (last {c.observations} days)</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Blueprint>
  );
}
