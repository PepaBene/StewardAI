"use client";

import { useCallback, useState } from "react";

import { AXIS_TEXT, Legend, SVG_BASE } from "@/components/charts/shared";
import { ResourceGate } from "@/components/company/primitives";
import { CoverageChip } from "@/components/insight/answer-strip";
import { ChartWithFooter } from "@/components/insight/chart-footer";
import { VerdictLine } from "@/components/insight/verdict-line";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { linePath, type Point } from "@/lib/charts/path";
import { linearScale, niceTicks } from "@/lib/charts/scale";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { COVERAGE_LABEL, cone, pointAt, type ConsensusView as View } from "@/lib/insight/consensus";
import { num } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

/*
 * Analyst targets (insight I8): the latest consensus with its spread and agreement, the price
 * against the stored targets with a cone to the 12-month target, and the track record once the
 * snapshots are a year old. A holding no source reaches says so; nothing is synthesised.
 */

const money = (v: unknown) => {
  const n = num(v as number | string | null);
  return n === null ? "—" : n.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const pct = (v: unknown, signed = false) => {
  const n = num(v as number | string | null);
  return n === null ? "—" : `${signed && n > 0 ? "+" : ""}${(n * 100).toFixed(1)}%`;
};

function TargetChart({ v }: { v: View }) {
  const [hover, setHover] = useState<number | null>(null);
  const prices = v.prices.map((p) => ({ t: new Date(p.date).getTime(), v: Number(p.close) }));
  const hist = v.history.map((h) => ({ t: new Date(h.as_of).getTime(), mean: num(h.mean), high: num(h.high), low: num(h.low) }));
  const c = cone(v.prices, v.history[v.history.length - 1]);
  const ts = [...prices.map((p) => p.t), ...hist.map((h) => h.t), ...(c ? [c.to.t] : [])];
  const vs = [...prices.map((p) => p.v), ...hist.flatMap((h) => [h.mean, h.high, h.low]), ...(c ? [c.to.high, c.to.low, c.to.mean] : [])].filter(
    (x): x is number => x !== null && Number.isFinite(x),
  );
  const W = 720;
  const H = 260;
  if (vs.length === 0 || ts.length === 0) {
    return <p className="text-sm text-text-muted">The stored snapshots carry no target figures and no prices are stored, so there is nothing to draw.</p>;
  }
  const ticks = niceTicks(Math.min(...vs) * 0.95, Math.max(...vs) * 1.05, 4);
  const x = linearScale([Math.min(...ts), Math.max(...ts)], [8, W - 64]);
  const y = linearScale([ticks[0], ticks[ticks.length - 1]], [H - 24, 10]);
  const step = (key: "mean" | "high" | "low"): Point[] => {
    const out: Point[] = [];
    hist.forEach((h, i) => {
      const val = h[key];
      if (val === null) return;
      const next = hist[i + 1]?.t ?? prices[prices.length - 1]?.t ?? h.t;
      out.push([x(h.t), y(val)], [x(next), y(val)]);
    });
    return out;
  };
  const top = step("high");
  const bottom = step("low");
  const bandPath = top.length && top.length === bottom.length ? `${linePath(top)} L${[...bottom].reverse().map(([a, b]) => `${a},${b}`).join(" L")} Z` : null;
  const hp = hover !== null ? prices[hover] : null;
  const snap = hp ? pointAt(v.history, hp.t) : null;
  return (
    <figure className="m-0 flex flex-col gap-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={SVG_BASE}
        role="img"
        aria-label="Price against the consensus target, its high-low range and the cone to the 12-month target"
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - box.left) / box.width) * W;
          let best = 0;
          prices.forEach((p, i) => {
            if (Math.abs(x(p.t) - px) < Math.abs(x(prices[best].t) - px)) best = i;
          });
          setHover(prices.length ? best : null);
        }}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line x1={8} x2={W - 64} y1={y(t)} y2={y(t)} className="stroke-chart-grid" />
            <text x={W - 58} y={y(t) + 4} fontSize={11} className={AXIS_TEXT}>
              {t}
            </text>
          </g>
        ))}
        {bandPath ? <path d={bandPath} className="fill-surface-muted" /> : null}
        {c ? (
          <path
            d={`M${x(c.from.t)},${y(c.from.v)} L${x(c.to.t)},${y(c.to.high ?? c.to.mean)} L${x(c.to.t)},${y(c.to.low ?? c.to.mean)} Z`}
            className="fill-chart-forecast opacity-15"
          />
        ) : null}
        {c ? <path d={`M${x(c.from.t)},${y(c.from.v)} L${x(c.to.t)},${y(c.to.mean)}`} className="stroke-chart-forecast" strokeDasharray="4 3" strokeWidth={1.5} fill="none" /> : null}
        <path d={linePath(step("mean"))} fill="none" strokeWidth={2} className="stroke-chart-forecast" />
        <path d={linePath(prices.map((p) => [x(p.t), y(p.v)] as Point))} fill="none" strokeWidth={2} className="stroke-chart-line" />
        {hp ? <line x1={x(hp.t)} x2={x(hp.t)} y1={8} y2={H - 24} className="stroke-text-muted" /> : null}
        <text x={8} y={H - 6} fontSize={11} className={AXIS_TEXT}>
          {formatDate(new Date(Math.min(...ts)).toISOString().slice(0, 10))}
        </text>
        <text x={W - 64} y={H - 6} fontSize={11} textAnchor="end" className={AXIS_TEXT}>
          {formatDate(new Date(Math.max(...ts)).toISOString().slice(0, 10))}
        </text>
      </svg>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <Legend
          items={[
            { label: "Price", swatch: "line", className: "bg-chart-line" },
            { label: "Mean target", swatch: "line", className: "bg-chart-forecast" },
            { label: "High-low target range", swatch: "square", className: "bg-surface-muted" },
            { label: "Cone to the 12-month target", swatch: "dashed", className: "border-chart-forecast" },
          ]}
        />
        <div aria-live="polite" className="min-h-[3.5rem] min-w-56 border border-border-soft px-2 py-1 text-xs">
          {hp ? (
            <>
              <p className="font-semibold">{formatDate(new Date(hp.t).toISOString().slice(0, 10))}</p>
              <p className="num">Price {money(hp.v)}</p>
              {snap ? (
                <p className="num">
                  Target {money(snap.mean)}
                  {num(snap.mean) !== null && hp.v > 0 ? ` (${pct(num(snap.mean)! / hp.v - 1, true)})` : ""}
                  {snap.count !== null ? `, ${snap.count} analysts` : ""}
                </p>
              ) : (
                <p className="text-text-muted">No snapshot yet on this date</p>
              )}
            </>
          ) : (
            <p className="text-text-muted">Hover the chart for a date&apos;s price and target.</p>
          )}
        </div>
      </div>
    </figure>
  );
}

function HistoryTable({ v }: { v: View }) {
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">Stored consensus snapshots</caption>
      <thead>
        <tr className="bg-surface-muted text-xs text-text-muted">
          {["Date", "Source", "Mean", "Low", "High", "Analysts", "Price then"].map((c) => (
            <th key={c} scope="col" className="px-2 py-1.5 text-right font-medium first:text-left">
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {[...v.history].reverse().map((h) => (
          <tr key={`${h.as_of}-${h.source}`} className="border-t border-border-soft">
            <th scope="row" className="px-2 py-1 text-left font-normal">{formatDate(h.as_of)}</th>
            <td className="px-2 py-1 text-right">{h.source}</td>
            <td className="num px-2 py-1 text-right">{money(h.mean)}</td>
            <td className="num px-2 py-1 text-right">{money(h.low)}</td>
            <td className="num px-2 py-1 text-right">{money(h.high)}</td>
            <td className="num px-2 py-1 text-right">{h.count ?? "—"}</td>
            <td className="num px-2 py-1 text-right">{money(h.price)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Body({ v }: { v: View }) {
  const l = v.latest;
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="space-y-3 px-[22px] py-5">
        <PanelHeading
          kicker="Stored snapshots from the providers your plan reaches"
          title="Analyst price targets"
          action={<CoverageChip tone={v.coverage === "complete" ? "neutral" : v.coverage}>{COVERAGE_LABEL[v.coverage]}</CoverageChip>}
        />
        <p className="text-sm text-text-muted">{v.coverage_reason}</p>
        {l ? (
          <>
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              {[
                ["Mean target", `${money(l.point.mean)} ${l.point.currency ?? ""}`],
                ["Low – high", `${money(l.point.low)} – ${money(l.point.high)}`],
                ["Against the price", pct(l.upside, true)],
                ["Spread over mean", pct(l.dispersion)],
                ["Analysts", l.point.count ?? "not given"],
              ].map(([k, val]) => (
                <div key={String(k)}>
                  <dt className="text-xs text-text-muted">{k}</dt>
                  <dd className="num text-lg font-semibold">{val}</dd>
                </div>
              ))}
            </dl>
            <VerdictLine verdict={l.agreement === "close" ? "good" : l.agreement === "wide" ? "caution" : l.agreement ? "neutral" : "unknown"}>
              {l.agreement_text}. Snapshot of {formatDate(l.point.as_of)} from {l.point.source}.
            </VerdictLine>
          </>
        ) : (
          <p className="border border-dashed border-border px-3 py-4 text-sm text-text-muted">No target is stored for this company, so none is shown anywhere in the app.</p>
        )}
        {v.sources.length ? (
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
            {v.sources.map((s) => (
              <li key={s.source}>
                {s.source}: {s.access_state.replace("_", " ")} on {formatDate(s.as_of)}
                {s.detail ? ` (${s.detail})` : ""}
              </li>
            ))}
          </ul>
        ) : null}
      </Blueprint>

      {v.history.length ? (
        <Blueprint className="space-y-3 px-[22px] py-5">
          <PanelHeading kicker={`${v.snapshots} snapshots`} title="Price against the targets" />
          {v.chart_available ? (
            <ChartWithFooter
              chart={<TargetChart v={v} />}
              table={<HistoryTable v={v} />}
              dataset="Weekly closes and stored consensus snapshots"
              asOf={v.footer.as_of}
              source={`${v.footer.source}. ${v.footer.basis}`}
            />
          ) : (
            <>
              <p className="text-sm text-text-muted">The chart appears once enough snapshots exist: {v.chart_reason}.</p>
              <HistoryTable v={v} />
            </>
          )}
        </Blueprint>
      ) : null}

      <Blueprint className={cn("space-y-3 px-[22px] py-5", !v.track.available && "border-dashed")}>
        <PanelHeading kicker="Did past targets come true" title="Track record" />
        {!v.track.available ? (
          <p className="text-sm text-text-muted">
            {v.track.starts ? `Available from ${formatDate(v.track.starts)}. ` : ""}
            {v.track.reason}
          </p>
        ) : (
          <>
            <p className="text-sm">
              {Math.round((num(v.track.hit_rate) ?? 0) * 100)}% of {v.track.n} targets were reached within 12 months; 12 months later the price sat on average{" "}
              {pct(v.track.mean_error, true)} from the target.
            </p>
            <table className="w-full text-sm">
              <caption className="sr-only">Targets a year old and what followed</caption>
              <thead>
                <tr className="bg-surface-muted text-xs text-text-muted">
                  {["Snapshot", "Target", "Price then", "12 months later", "Reached", "Gap"].map((c) => (
                    <th key={c} scope="col" className="px-2 py-1.5 text-right font-medium first:text-left">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(v.track.rows ?? []).map((r) => (
                  <tr key={r.as_of} className="border-t border-border-soft">
                    <th scope="row" className="px-2 py-1 text-left font-normal">{formatDate(r.as_of)}</th>
                    <td className="num px-2 py-1 text-right">{money(r.target)}</td>
                    <td className="num px-2 py-1 text-right">{money(r.price_then)}</td>
                    <td className="num px-2 py-1 text-right">{money(r.price_after)}</td>
                    <td className="px-2 py-1 text-right">{r.reached === null ? "unknown" : r.reached ? "yes" : "no"}</td>
                    <td className="num px-2 py-1 text-right">{pct(r.error, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Blueprint>
      <p className="text-xs text-text-muted">Analyst targets are other people&apos;s opinions, shown as stored. They are not a value or a recommendation.</p>
    </div>
  );
}

export function ConsensusView({ ticker }: { ticker: string }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/companies/{ticker}/consensus", { params: { path: { ticker } } }), [ticker]);
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="the analyst targets">
      {(v) => <Body v={v} />}
    </ResourceGate>
  );
}
