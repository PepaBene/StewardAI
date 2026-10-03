"use client";

import { ArrowDownRightIcon, ArrowUpRightIcon, MinusIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { AXIS_TEXT, SVG_BASE } from "@/components/charts/shared";
import { ChartFrame, Segmented } from "@/components/data-state";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { linePath } from "@/lib/charts/path";
import { bandScale, extent, linearScale, niceTicks } from "@/lib/charts/scale";
import { buildEstimates, forwardMetrics, forwardState, isDateVintage, safeHref, vintageDate } from "@/lib/financials/logic";
import { FORWARD_LABELS, type FactRow, type ForwardKind, type ForwardPayload } from "@/lib/financials/types";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const W = 820;
const H = 330;
const OFFSET: Record<ForwardKind, number> = {
  company_guidance: -26,
  published_analyst_consensus: 0,
  owner_scenario: 26,
  event_calendar_estimate: 40,
};

/** A stated vintage as a date when it is one; free text ("Q1 2026 preview") is shown as the provider wrote it. */
function vLabel(v: string | null): string {
  return v === null ? "—" : isDateVintage(v) ? formatDate(v.slice(0, 10)) : v;
}

function n(v: number | null, digits = 2): string {
  return v === null ? "—" : formatNumber(v, Math.abs(v) >= 1000 ? 0 : digits);
}

/**
 * Actual, guidance, consensus, scenario and event-calendar figures for one metric, each with
 * its own mark so they cannot be confused: actuals a solid line, guidance a bracketed range,
 * consensus a band with the mean (bar) and the analyst count the provider states, an owner
 * scenario a dashed line with diamonds, a calendar estimate a hollow circle. One consensus
 * series (provider, currency, basis) is drawn at a time, anything else is listed with the
 * reason it is not plotted, and revision slopes compare only snapshots of that one series.
 */
export function EstimatesFan({ payload, facts, asOf }: { payload: ForwardPayload; facts: readonly FactRow[]; asOf: string | null }) {
  const metrics = useMemo(() => forwardMetrics(payload), [payload]);
  const [metricChoice, setMetric] = useState<string | null>(null);
  const [seriesChoice, setSeries] = useState<string | null>(null);
  const metric = metricChoice && metrics.includes(metricChoice) ? metricChoice : metrics[0];
  const model = useMemo(() => (metric ? buildEstimates(payload, facts, metric, seriesChoice) : null), [payload, facts, metric, seriesChoice]);

  const base = { id: "estimates", kicker: "Estimates boundary" };
  if (!model) {
    return (
      <ChartFrame
        {...base}
        title="What was reported, guided, expected and assumed"
        hasValue={false}
        provenance={{ source: "Stored forward observations", asOf: null, state: "not_covered", reason: "No forward observation of any kind (guidance, consensus, owner scenario, event-calendar estimate) is stored for this holding. The app never fills one in." }}
      >
        {null}
      </ChartFrame>
    );
  }
  const status = forwardState(model);
  const values = [...model.actuals.map((a) => a.value), ...model.points.flatMap((f) => [f.low, f.value, f.high])].filter((x): x is number => x !== null);
  const ext = extent(values);
  const periods = model.periods;
  const hasChart = ext !== null && periods.length > 0;
  const ticks = ext ? niceTicks(ext[0], ext[1], 5) : [0, 1];
  const y = linearScale([ticks[0], ticks[ticks.length - 1]], [H - 34, 14]);
  const band = bandScale(Math.max(periods.length, 1), [0, W - 52], { paddingInner: 0.1 });
  const cx = (p: string) => band.x(periods.indexOf(p)) + band.bandwidth / 2;
  const actualPts = model.actuals.filter((a) => periods.includes(a.period)).map((a) => [cx(a.period), y(a.value)] as const);
  const scenario = model.points.filter((f) => f.kind === "owner_scenario" && f.value !== null).sort((a, b) => a.fiscalPeriod.localeCompare(b.fiscalPeriod));
  const firstConsensus = model.points.find((p) => p.kind === "published_analyst_consensus" && p.value !== null);
  const option = model.seriesOptions.find((s) => s.id === model.seriesId);
  const providers = [...new Set(model.points.map((p) => p.provider).filter((p): p is string => !!p))];
  const vintages = model.points.map((p) => vintageDate(p)).sort();
  const cur = model.currency ?? "currency not stated";

  return (
    <ChartFrame
      {...base}
      title={`${metric}: what was reported, guided, expected and assumed`}
      takeaway={
        firstConsensus
          ? `${firstConsensus.fiscalPeriod} consensus mean ${n(firstConsensus.value)} (${firstConsensus.analystCount === null ? "analyst count not stated" : `${firstConsensus.analystCount} analysts`}${firstConsensus.low !== null && firstConsensus.high !== null ? `, range ${n(firstConsensus.low)} to ${n(firstConsensus.high)}` : ""}, ${option?.provider ?? "provider not stated"}).${model.actuals.length ? ` Last actual: ${n(model.actuals[model.actuals.length - 1].value)} (${model.actuals[model.actuals.length - 1].period}).` : ""}`
          : "No published analyst consensus is stored for this metric; only the figures below exist."
      }
      units={`${cur}; five kinds of figure, never merged; analyst count only where the provider states it${asOf ? ` · as known on ${formatDate(asOf)}` : ""}${option?.basis ? ` · consensus basis: ${option.basis}` : ""}`}
      hasValue={model.points.length > 0}
      controls={
        <>
          {metrics.length > 1 ? <Segmented label="Metric" value={metric} onChange={setMetric} options={metrics.map((m) => ({ id: m, label: m }))} /> : null}
          {model.seriesOptions.length > 1 ? (
            <label className="flex items-center gap-1 text-xs text-text-muted">
              <span>Consensus series</span>
              <select value={model.seriesId ?? ""} onChange={(e) => setSeries(e.target.value)} className="max-w-[16rem] border border-border bg-surface-raised px-2 py-1 text-xs text-text">
                {model.seriesOptions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {`${s.provider ?? "unknown provider"} · ${s.currency ?? "no currency"} · ${s.basis ?? "no basis"} (${s.periods} periods)`}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </>
      }
      provenance={{
        source: providers.length ? providers.join(" · ") : "Stored forward observations",
        sourceUrl: safeHref(model.points.find((p) => safeHref(p.sourceUrl))?.sourceUrl),
        asOf: vintages.length ? vintages[vintages.length - 1] : null,
        knownAt: model.points.map((p) => p.knownAt).sort().pop() ?? null,
        method: "Estimates exactly as published by their provider; the app never fills a missing estimate with a model figure",
        state: status.state,
        reason: status.reason,
        coverage: periods.length > 0 ? { covered: periods.length - model.consensusGaps.length, total: periods.length, unit: "periods with consensus" } : null,
      }}
      table={{
        caption: `${metric}: every figure by period and kind`,
        columns: ["Period", "Kind", "Low", "Mean / value", "High", "Analysts", "Basis", "Vintage", "Provider"],
        rows: [
          ...model.actuals.map((a) => [a.period, FORWARD_LABELS.actual, "—", n(a.value), "—", "—", "as filed", formatDate(a.knownAt.slice(0, 10)), "filing"]),
          ...model.points.map((f) => [f.fiscalPeriod, FORWARD_LABELS[f.kind], n(f.low), n(f.value), n(f.high), f.analystCount === null ? "not stated" : f.analystCount, f.basis ?? "not stated", vLabel(f.vintage), f.provider ?? "—"]),
          ...model.unplotted.map(({ obs: f, reason }) => [f.fiscalPeriod, `${FORWARD_LABELS[f.kind]} (not plotted)`, n(f.low), n(f.value), n(f.high), f.analystCount === null ? "not stated" : f.analystCount, f.basis ?? "not stated", vLabel(f.vintage), `${f.provider ?? "—"}: ${reason}`]),
        ],
      }}
    >
      {hasChart ? (
        <div className="relative overflow-x-auto">
          <svg viewBox={`0 0 ${W} ${H}`} className={cn(SVG_BASE)} style={{ minWidth: `${Math.max(34, periods.length * 6)}rem` }} role="img" aria-label={`${metric} by period: ${model.actuals.length} actuals, guidance, consensus, owner scenario and calendar estimates`}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={0} x2={W - 52} y1={y(t)} y2={y(t)} className="stroke-chart-grid" strokeWidth={1} shapeRendering="crispEdges" />
                <text x={W} y={y(t) + 4} textAnchor="end" fontSize={11} className={AXIS_TEXT}>
                  {n(t)}
                </text>
              </g>
            ))}
            {periods.map((p) => (
              <text key={p} x={cx(p)} y={H - 10} textAnchor="middle" fontSize={11} className={AXIS_TEXT}>
                {p}
                {model.actuals.some((a) => a.period === p) ? " A" : " E"}
              </text>
            ))}
            {model.consensusGaps
              .filter((p) => periods.includes(p))
              .map((p) => (
                <g key={p}>
                  <text x={cx(p)} y={22} textAnchor="middle" fontSize={10} className="fill-text-muted">
                    no consensus
                  </text>
                </g>
              ))}
            {actualPts.length > 1 ? <path d={linePath(actualPts)} fill="none" className="stroke-text" strokeWidth={2} strokeLinejoin="round" /> : null}
            {model.actuals
              .filter((a) => periods.includes(a.period))
              .map((a) => (
                <circle key={a.period} cx={cx(a.period)} cy={y(a.value)} r={4} className="fill-text">
                  <title>{`${a.period} actual: ${n(a.value)}`}</title>
                </circle>
              ))}
            {scenario.length > 1 ? <path d={linePath(scenario.map((s) => [cx(s.fiscalPeriod) + OFFSET.owner_scenario, y(s.value as number)] as const))} fill="none" className="stroke-text-muted" strokeWidth={1.5} strokeDasharray="5 4" /> : null}
            {model.points.map((f) => {
              const x = cx(f.fiscalPeriod) + OFFSET[f.kind];
              const key = `${f.id}`;
              if (f.kind === "company_guidance") {
                if (f.low !== null && f.high !== null) {
                  return (
                    <g key={key}>
                      <line x1={x} x2={x} y1={y(f.high)} y2={y(f.low)} className="stroke-text" strokeWidth={2} />
                      <line x1={x - 5} x2={x + 5} y1={y(f.high)} y2={y(f.high)} className="stroke-text" strokeWidth={2} />
                      <line x1={x - 5} x2={x + 5} y1={y(f.low)} y2={y(f.low)} className="stroke-text" strokeWidth={2} />
                      <text x={x} y={y(f.high) - 6} textAnchor="middle" fontSize={10} className="fill-text font-semibold">
                        G
                      </text>
                      <title>{`${f.fiscalPeriod} company guidance ${n(f.low)} to ${n(f.high)} (${f.provider ?? "provider not stated"}, ${f.vintage ?? formatDate(f.knownAt.slice(0, 10))})`}</title>
                    </g>
                  );
                }
                return f.value !== null ? (
                  <g key={key}>
                    <line x1={x - 6} x2={x + 6} y1={y(f.value)} y2={y(f.value)} className="stroke-text" strokeWidth={2.5} />
                    <text x={x} y={y(f.value) - 6} textAnchor="middle" fontSize={10} className="fill-text font-semibold">
                      G
                    </text>
                    <title>{`${f.fiscalPeriod} company guidance ${n(f.value)} (point value)`}</title>
                  </g>
                ) : null;
              }
              if (f.kind === "published_analyst_consensus" && f.value !== null) {
                return (
                  <g key={key}>
                    {f.low !== null && f.high !== null ? (
                      <rect x={x - 11} y={y(f.high)} width={22} height={Math.max(1, y(f.low) - y(f.high))} className="fill-accent-soft stroke-accent" strokeWidth={1.25}>
                        <title>{`${f.fiscalPeriod} consensus ${n(f.low)} to ${n(f.high)}, mean ${n(f.value)}, ${f.analystCount ?? "unknown"} analysts (${f.provider ?? "provider not stated"}, ${f.vintage ?? ""})`}</title>
                      </rect>
                    ) : null}
                    <line x1={x - 14} x2={x + 14} y1={y(f.value)} y2={y(f.value)} className="stroke-accent-ramp-1" strokeWidth={3}>
                      <title>{`${f.fiscalPeriod} consensus mean ${n(f.value)}`}</title>
                    </line>
                    <text x={x} y={(f.high !== null ? y(f.high) : y(f.value)) - 6} textAnchor="middle" fontSize={10} className="fill-text-muted num">
                      {f.analystCount === null ? "n n/a" : `n=${f.analystCount}`}
                    </text>
                  </g>
                );
              }
              if (f.kind === "owner_scenario" && f.value !== null) {
                return (
                  <g key={key}>
                    {f.low !== null && f.high !== null ? <line x1={x} x2={x} y1={y(f.high)} y2={y(f.low)} className="stroke-text-muted" strokeWidth={1} strokeDasharray="1 3" /> : null}
                    <rect x={x - 4} y={y(f.value) - 4} width={8} height={8} transform={`rotate(45 ${x} ${y(f.value)})`} className="fill-surface-raised stroke-text-muted" strokeWidth={1.5}>
                      <title>{`${f.fiscalPeriod} owner scenario ${n(f.value)} (${f.provider ?? "owner"})`}</title>
                    </rect>
                  </g>
                );
              }
              if (f.kind === "event_calendar_estimate" && f.value !== null) {
                return (
                  <circle key={key} cx={x} cy={y(f.value)} r={4.5} className="fill-surface-raised stroke-text" strokeWidth={1.5}>
                    <title>{`${f.fiscalPeriod} event-calendar estimate ${n(f.value)} (${f.provider ?? "provider not stated"}, ${f.basis ?? "basis not stated"}); no analyst count`}</title>
                  </circle>
                );
              }
              return null;
            })}
          </svg>
        </div>
      ) : (
        <p role="status" className="border border-dashed border-border px-3 py-2 text-sm text-text-muted">
          Nothing can be plotted for this metric in one currency; the figures that exist are in the table below.
        </p>
      )}
      <ul className="m-0 flex list-none flex-wrap gap-x-5 gap-y-1 p-0 text-[11px] text-text-muted">
        <li className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block h-0.5 w-4 bg-text" /> Actual (A, as filed)
        </li>
        <li className="inline-flex items-center gap-1.5">
          <b aria-hidden="true" className="font-semibold text-text">G</b> Company guidance
        </li>
        <li className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block h-3 w-3 border border-accent bg-accent-soft" /> Consensus range, bar = mean, n = analysts
        </li>
        <li className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block size-2 rotate-45 border border-text-muted" /> Owner scenario
        </li>
        <li className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block size-2.5 rounded-full border border-text" /> Event-calendar estimate
        </li>
      </ul>
      {model.actualsNote ? (
        <p role="note" className="border-l-[3px] border-l-warning-signal bg-warning-signal-subtle px-3 py-1.5 text-sm">
          {model.actualsNote}
        </p>
      ) : null}

      <section aria-labelledby="revisions-heading" className="flex min-w-0 flex-col gap-1.5">
        <h3 id="revisions-heading" className="font-heading text-lg font-semibold">
          Consensus revisions between comparable snapshots
        </h3>
        {model.revisions.length === 0 ? (
          <p className="text-sm text-text-muted">No consensus series is stored for this metric.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm">
            {model.revisions.map(({ period, slope, reason }) => (
              <li key={period} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 border-b border-border-soft pb-1.5">
                <span className="w-20 shrink-0 font-medium">{period}</span>
                {slope ? (
                  <>
                    {slope.change > 0 ? <ArrowUpRightIcon aria-hidden="true" className="size-4" /> : slope.change < 0 ? <ArrowDownRightIcon aria-hidden="true" className="size-4" /> : <MinusIcon aria-hidden="true" className="size-4" />}
                    <span className="num">
                      {n(slope.from.mean)} → {n(slope.to.mean)}
                    </span>
                    <span className="num font-semibold">{slope.pct === null ? "" : formatPct(slope.pct, 1, true)}</span>
                    <span className="text-xs text-text-muted">
                      {formatDate(slope.from.vintage)} to {formatDate(slope.to.vintage)} ({slope.days} days, {slope.snapshots} snapshots, same provider, currency and basis)
                    </span>
                  </>
                ) : (
                  <span className="text-xs text-text-muted">{reason}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
      {model.unplotted.length > 0 ? (
        <section aria-labelledby="unplotted-heading" className="flex min-w-0 flex-col gap-1">
          <h3 id="unplotted-heading" className="font-heading text-lg font-semibold">
            Figures not plotted
          </h3>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
            {model.unplotted.map(({ obs: o, reason }) => (
              <li key={o.id} className="border-b border-border-soft pb-1">
                <span className="font-medium">{o.fiscalPeriod}</span> {FORWARD_LABELS[o.kind]} ({o.provider ?? "provider not stated"}): {reason}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </ChartFrame>
  );
}
