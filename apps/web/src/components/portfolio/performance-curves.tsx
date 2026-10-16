"use client";

import { DrawdownChart, WaterfallChart, type WaterfallStep } from "@/components/charts";
import { LimitedState } from "@/components/insight/limited-state";
import { VerdictLine } from "@/components/insight/verdict-line";
import { ReasonOnce } from "@/components/ui/reason-once";
import { SkeletonPlot } from "@/components/ui/skeleton";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatDate, formatMoney, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";

import { ChartFrame } from "./chart-frame";

type Drawdown = components["schemas"]["DrawdownResponse"];
type Waterfall = components["schemas"]["WaterfallResponse"];

const fetchDrawdown = () => getBrowserApiClient().GET("/portfolio/performance/drawdown", { params: { query: { days: 365 * 3 } } });
const fetchWaterfall = () => getBrowserApiClient().GET("/portfolio/performance/waterfall", { params: { query: { days: 365 } } });

/** How deep the worst fall was and whether the portfolio has climbed back. */
export function drawdownSentence(d: Drawdown): { verdict: "good" | "caution" | "bad" | "neutral"; text: string } | null {
  if (d.max_drawdown === null || d.max_drawdown === undefined) return null;
  const depth = formatPercent(d.max_drawdown, { decimals: 1 });
  const span = d.peak_date && d.trough_date ? ` from ${formatDate(d.peak_date)} to ${formatDate(d.trough_date)}` : "";
  if (d.max_drawdown > -0.001) return { verdict: "good", text: "No fall from a high in this period." };
  const tail = d.recovery_date
    ? `, back at the old high by ${formatDate(d.recovery_date)}.`
    : `, still ${formatPercent(d.current_drawdown ?? d.max_drawdown, { decimals: 1 })} below the old high.`;
  const verdict = d.max_drawdown <= -0.2 ? "bad" : d.max_drawdown <= -0.1 ? "caution" : "neutral";
  return { verdict, text: `The deepest fall was ${depth}${span}${tail}` };
}

export function DrawdownSection() {
  const { state } = useApiResource(fetchDrawdown);
  if (state.kind === "loading") {
    return (
      <ChartFrame title="Falls from a high">
        <SkeletonPlot height={140} />
      </ChartFrame>
    );
  }
  if (state.kind === "error") return null;
  const d = state.data as Drawdown;
  const reading = drawdownSentence(d);
  return (
    <ChartFrame
      title="Falls from a high"
      reading={reading ? <VerdictLine verdict={reading.verdict}>{reading.text}</VerdictLine> : null}
      dataset="Portfolio history, deposits and withdrawals removed"
      asOf={d.as_of}
      learnHref="/methods#drawdown"
    >
      {d.points.length < 2 ? (
        <LimitedState fixHref="/performance/ledger" since={d.as_of ? formatDate(d.as_of) : undefined}>
          {(d.reasons ?? [])[0] ?? "Needs at least two daily snapshots."}
        </LimitedState>
      ) : (
        // The chart takes levels; 1 + drawdown has a running peak of 1, so it redraws the same curve.
        <DrawdownChart data={d.points.map((p) => ({ date: p.date, value: 1 + p.drawdown }))} label="Portfolio" height={140} />
      )}
    </ChartFrame>
  );
}

/** The bridge steps in chart form; unknown steps are left out and named in the caveat. */
export function waterfallSteps(w: Waterfall): WaterfallStep[] {
  return w.steps
    .filter((s) => s.value !== null && s.value !== undefined)
    .map((s) => ({ id: s.key, label: s.label, value: Number(s.value), kind: s.kind, derived: s.key === "unattributed" }));
}

export function WaterfallSection() {
  const { state } = useApiResource(fetchWaterfall);
  if (state.kind === "loading") {
    return (
      <ChartFrame title="Where the year's change came from">
        <SkeletonPlot height={200} />
      </ChartFrame>
    );
  }
  if (state.kind === "error") return null;
  const w = state.data as Waterfall;
  const steps = waterfallSteps(w);
  const ccy = w.base_currency ?? "EUR";
  const missing = w.steps.filter((s) => s.value === null || s.value === undefined).map((s) => `${s.label}: ${s.reason ?? "not known"}`);
  const price = w.steps.find((s) => s.key === "price")?.value;
  return (
    <ChartFrame
      title="Where the year's change came from"
      kicker={w.start && w.end ? `${formatDate(w.start)} to ${formatDate(w.end)}` : undefined}
      reading={
        price === null || price === undefined ? null : (
          <VerdictLine verdict="neutral">
            Price moves {price >= 0 ? "added" : "took away"} {formatMoney(Math.abs(price), ccy, { decimals: 0 })}.
          </VerdictLine>
        )
      }
      dataset="Attribution bridge, base currency"
      asOf={w.end}
      learnHref="/performance/attribution"
    >
      {w.status === "unavailable" || steps.length < 3 ? (
        <LimitedState fixHref="/performance/ledger">{(w.reasons ?? [])[0] ?? "The bridge needs a full ledger for the period."}</LimitedState>
      ) : (
        <div className="space-y-3">
          <WaterfallChart steps={steps} label="Change in portfolio value, step by step" formatValue={(v) => formatMoney(v, ccy, { decimals: 0 })} />
          <ReasonOnce reasons={[...missing, ...(w.reasons ?? [])]} />
        </div>
      )}
    </ChartFrame>
  );
}
