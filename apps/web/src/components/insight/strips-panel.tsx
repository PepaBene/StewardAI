"use client";

import { useCallback, useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { components } from "@/lib/api/schema";
import { getBrowserApiClient } from "@/lib/api/client";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { num } from "@/lib/insight/insight";
import { cn } from "@/lib/utils";

import { ChartWithFooter } from "./chart-footer";
import { DotStrip } from "./dot-strip";

type PortfolioStrips = components["schemas"]["PortfolioStrips"];
type Strip = PortfolioStrips["strips"][number];
type Basis = "headline" | "dcf_default" | "dcf_base" | "multiple_base" | "thesis_target" | "consensus_target" | "peer_multiple";

const fmt = (unit: Strip["unit"]) => (v: number) => (unit === "multiple" ? `${v.toFixed(1)}×` : `${(v * 100).toFixed(Math.abs(v) < 0.1 ? 1 : 0)}%`);

/*
 * Ranked dot strips per axis (insight I4.5), for Allocation › Fundamentals: one strip per metric,
 * each holding a dot, the portfolio's value-weighted mean as the reference line, tinted zones from
 * the Checks limits, and a table twin. The valuation strip has basis tabs that save nothing.
 */
export function StripsPanel() {
  const [basis, setBasis] = useState<Basis>("headline");
  const fetcher = useCallback(() => getBrowserApiClient().GET("/portfolio/strips", { params: { query: { basis } } }), [basis]);
  const { state } = useApiResource(fetcher);
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Each dot is a holding" title="Where each holding sits" />
      <div className="mt-3">
        <ResourceGate state={state} label="holding strips">
          {(d: PortfolioStrips) => (
            <div className="flex flex-col gap-6">
              {d.strips.map((s) => (
                <StripBlock
                  key={s.metric}
                  s={s}
                  tabs={
                    s.metric === "valuation" ? (
                      <div role="tablist" aria-label="Fair-value basis" className="flex flex-wrap gap-1">
                        {Object.entries(d.bases).map(([k, label]) => (
                          <button
                            key={k}
                            type="button"
                            role="tab"
                            aria-selected={basis === k}
                            onClick={() => setBasis(k as Basis)}
                            className={cn("border px-2 py-0.5 text-xs", basis === k ? "border-accent bg-accent-soft text-accent-ink" : "border-border text-text-muted")}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    ) : null
                  }
                />
              ))}
              <p className="text-xs text-text-faint">{d.note}</p>
            </div>
          )}
        </ResourceGate>
      </div>
    </Blueprint>
  );
}

function StripBlock({ s, tabs }: { s: Strip; tabs: React.ReactNode }) {
  const format = fmt(s.unit);
  const portfolio = num(s.portfolio);
  const dots = s.dots.map((d) => ({ key: d.ticker, label: d.name ?? d.ticker, value: num(d.value) }));
  const zones = s.zones.map((z) => ({ from: num(z.lo) ?? -1e9, to: num(z.hi) ?? 1e9, tone: z.tone, label: z.label }));
  return (
    <section aria-label={s.label}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold">{s.label}</h3>
        <span className="text-xs text-text-muted">{s.basis}</span>
      </div>
      {tabs ? <div className="mt-1.5">{tabs}</div> : null}
      <ChartWithFooter
        className="mt-2"
        chart={
          <DotStrip
            dots={dots}
            refs={portfolio !== null ? [{ label: "Portfolio", value: portfolio }] : []}
            zones={zones}
            format={format}
            label={s.label}
          />
        }
        table={
          <table className="w-full text-sm">
            <caption className="sr-only">{s.label} by holding</caption>
            <thead>
              <tr className="bg-surface-muted text-xs text-text-muted">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">Holding</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Weight</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">{s.label}</th>
              </tr>
            </thead>
            <tbody>
              {s.dots.map((d) => (
                <tr key={d.ticker} className="border-t border-border-soft">
                  <th scope="row" className="px-3 py-1 text-left font-normal">{d.name ?? d.ticker}</th>
                  <td className="num px-3 py-1 text-right">{format(num(d.weight) ?? 0)}</td>
                  <td className="num px-3 py-1 text-right">{num(d.value) === null ? <span className="text-text-faint" title={d.reason ?? undefined}>—</span> : format(num(d.value)!)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        }
        dataset={`${s.dots.filter((d) => d.value !== null).length} of ${s.dots.length} holdings placed; ${s.benchmark_note}`}
      />
    </section>
  );
}
