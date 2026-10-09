"use client";

import { BanIcon, CheckCircle2Icon, CircleDashedIcon } from "lucide-react";

import { BarChart, RevisionLine, Treemap, WaterfallChart } from "@/components/charts";
import {
  axisLabel,
  bridgeTiles,
  cashWaterfall,
  compactMoney,
  gates,
  maturityCheck,
  METRIC_LABEL,
  metricsPresent,
  periodLabel,
  seriesFor,
  type MaturityBucket,
  type SegmentBridge,
  type StatementsOut,
} from "@/lib/disclosure/financials";
import { formatStamp } from "@/lib/disclosure/model";
import { cn } from "@/lib/utils";

const ORDER = ["revenue", "gross_profit", "operating_income", "net_income", "operating_cash_flow", "capex", "cash", "total_debt"];
const money = (n: number) => compactMoney(n);

/**
 * As-filed history, one small chart per metric. The solid line is the latest value known at the
 * cutoff; the dashed line is what the first filing said, and a square marks every period that was
 * restated. Where nothing was restated the two lines coincide, which reads as "no revisions".
 */
export function StatementHistory({ out }: { out: StatementsOut }) {
  const metrics = metricsPresent(out.history, ORDER);
  if (metrics.length === 0) return <p className="text-sm text-text-muted">No statement values had been filed for this company by the chosen date.</p>;
  const restated = out.history.filter((p) => p.restated);
  return (
    <div className="space-y-3">
      <ul className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-text-muted" aria-label="How to read the charts">
        <li><span aria-hidden="true" className="mr-1.5 inline-block h-0.5 w-5 bg-chart-line align-middle" />Latest value known at the date</li>
        <li><span aria-hidden="true" className="mr-1.5 inline-block w-5 border-t-2 border-dashed border-chart-benchmark align-middle" />First reported</li>
        <li><span aria-hidden="true" className="mr-1.5 inline-block size-2 bg-text align-middle" />Restated since</li>
      </ul>
      <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2 xl:grid-cols-3">
        {metrics.map((m) => {
          const latest = out.history.filter((p) => p.metric === m).at(-1)!;
          return (
            <figure key={m} className="min-w-0 space-y-1">
              <figcaption className="flex items-baseline justify-between gap-2">
                <span className="text-sm font-medium">{METRIC_LABEL[m] ?? m}</span>
                <span className="num text-sm">{latest.value == null ? "Not reported" : money(Number(latest.value))}</span>
              </figcaption>
              <RevisionLine points={seriesFor(out.history, m)} label={`${METRIC_LABEL[m] ?? m}, first reported against latest`} formatValue={money} width={280} height={72} className="w-full" />
              {latest.restated ? (
                <p className="text-xs text-text-muted">
                  {periodLabel(latest.period_end)} restated from <span className="num">{money(Number(latest.original_value))}</span> to <span className="num">{money(Number(latest.value))}</span> on{" "}
                  <span className="num">{formatStamp(latest.known_at)}</span>.
                </p>
              ) : null}
            </figure>
          );
        })}
      </div>
      {restated.length === 0 ? <p className="text-xs text-text-muted">No value shown here was restated by a later filing.</p> : null}
    </div>
  );
}

/** Operating cash flow, less capex, to free cash flow, for one period. Says why when it cannot be drawn. */
export function CashConversion({ out, end }: { out: StatementsOut; end: string | null }) {
  if (!end) return <p className="text-sm text-text-muted">No period is available.</p>;
  const steps = cashWaterfall(out.history, end);
  const derived = out.derived.find((d) => d.period_end === end);
  if (!steps) {
    return (
      <p className="text-sm text-text-muted">
        {periodLabel(end)}: operating cash flow and capex are not both known with a usable sign, so free cash flow is not worked out. A reviewer decides this one.
      </p>
    );
  }
  return (
    <div className="space-y-2">
      <WaterfallChart steps={steps} label={`Cash conversion, ${periodLabel(end)}`} formatValue={money} className="w-full max-w-2xl" />
      {derived ? (
        <p className="text-xs text-text-muted">
          Free cash flow is worked out here ({derived.formula}, {derived.formula_version}) from filings <span className="num">{[...new Set(derived.inputs)].join(", ")}</span>. The dashed bar is not a reported figure.
        </p>
      ) : null}
    </div>
  );
}

/** Can each period feed a valuation? A failed or unsupported check blocks it, and the reason is shown. */
export function ValuationGates({ out }: { out: StatementsOut }) {
  const rows = gates(out);
  if (rows.length === 0) return null;
  return (
    <ul className="space-y-1.5 text-sm">
      {rows.map((g) => (
        <li key={g.end} className="flex flex-wrap items-start gap-x-3">
          <span className="num w-16 shrink-0 font-medium">{periodLabel(g.end)}</span>
          {g.usable ? (
            <span className="inline-flex items-center gap-1.5"><CheckCircle2Icon aria-hidden="true" className="size-4 text-verified" />Checks passed. May feed a valuation.</span>
          ) : (
            <span className="inline-flex flex-col gap-0.5">
              <span className="inline-flex items-center gap-1.5 font-medium"><BanIcon aria-hidden="true" className="size-4 text-warning-signal" />Blocked from valuation</span>
              {g.failing.map((c) => (
                <span key={c.name} className="text-text-muted">{c.status === "fail" ? "Failed" : "Cannot be checked"}: {c.detail}</span>
              ))}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Each segment axis is bridged to reported revenue on its own; axes are never added together. */
export function SegmentBridgePanel({ bridge }: { bridge: SegmentBridge }) {
  if (bridge.total == null || bridge.axes.length === 0) {
    return <p className="text-sm text-text-muted">{bridge.notes[0] ?? "No segment values are known for this period."}</p>;
  }
  const total = Number(bridge.total);
  return (
    <div className="space-y-5">
      <p className="text-sm">
        Reported revenue, {periodLabel(bridge.period_end)}: <strong className="num">{money(total)}</strong>
      </p>
      {bridge.axes.map((a) => {
        const tiles = bridgeTiles(a);
        return (
          <section key={a.axis} aria-label={`${axisLabel(a.axis)} breakdown`} className="space-y-2">
            <h3 className="text-base font-semibold">By {axisLabel(a.axis).toLowerCase()}</h3>
            <div className="grid gap-4 md:grid-cols-[minmax(0,26rem)_1fr]">
              <Treemap items={tiles} scale={false} aspectRatio={1.6} label={`${axisLabel(a.axis)} breakdown of revenue`} formatWeight={(w) => `${((w / Number(a.members_sum)) * 100).toFixed(0)}%`} formatChange={() => ""} />
              <table className="w-full self-start border-collapse text-sm">
                <caption className="sr-only">{axisLabel(a.axis)} members against reported revenue</caption>
                <thead>
                  <tr className="border-b border-border text-left">
                    <th scope="col" className="py-1.5 pr-3 font-medium">Member</th>
                    <th scope="col" className="py-1.5 text-right font-medium">Revenue</th>
                  </tr>
                </thead>
                <tbody>
                  {a.members.map((m) => (
                    <tr key={m.member} className="border-b border-border-soft">
                      <th scope="row" className="py-1.5 pr-3 text-left font-normal">{m.member}{m.eliminations ? <span className="text-text-muted"> (eliminations)</span> : null}</th>
                      <td className="num py-1.5 text-right">{money(Number(m.value))}</td>
                    </tr>
                  ))}
                  <tr className={cn("border-b border-border", !a.reconciles && "bg-warning-signal-subtle")}>
                    <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                      {a.reconciles ? "Accounts for the total" : "Not accounted for"}
                    </th>
                    <td className="num py-1.5 text-right font-medium">{a.reconciles ? "✓" : money(Number(a.unallocated))}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
      <ul className="space-y-0.5 text-xs text-text-muted">
        {bridge.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </div>
  );
}

/** Debt due by year, per currency. Notes are stored as text blocks, so live data may have none yet. */
export function DebtMaturities({ items, sample }: { items: { currency: string; total: number; buckets: MaturityBucket[] }[]; sample: boolean }) {
  if (items.length === 0) {
    return (
      <p className="flex items-start gap-2 text-sm text-text-muted">
        <CircleDashedIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        Debt maturities are not extracted into figures yet: filings carry them as note text, and a bucket is only shown once it adds up to the reported total.
      </p>
    );
  }
  return (
    <div className="grid gap-6 md:grid-cols-2">
      {items.map((it) => {
        const check = maturityCheck(it.buckets, it.total);
        return (
          <figure key={it.currency} className="min-w-0 space-y-1">
            <figcaption className="flex items-baseline justify-between gap-2 text-sm">
              <span className="font-medium">{it.currency} debt by year due</span>
              <span className="num text-text-muted">Total {money(it.total)}</span>
            </figcaption>
            <BarChart data={it.buckets.map((b) => ({ label: b.label, value: b.value }))} formatValue={money} label={`${it.currency} debt maturities`} width={420} height={170} className="w-full" />
            <p className="text-xs text-text-muted">
              {check.status === "pass" ? "Buckets add up to the reported total." : check.status === "fail" ? "Buckets do not add up to the reported total: held for review." : "Cannot be checked."}
              {sample ? " Sample figures." : ""}
            </p>
          </figure>
        );
      })}
    </div>
  );
}
