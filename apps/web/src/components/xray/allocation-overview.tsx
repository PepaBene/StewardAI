"use client";

import { ChevronRightIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { LorenzChart } from "@/components/charts";
import { BandGauge, type GaugeZone } from "@/components/insight/band-gauge";
import { LimitedState } from "@/components/insight/limited-state";
import { VerdictLine } from "@/components/insight/verdict-line";
import { ChartFrame } from "@/components/portfolio/chart-frame";
import { ReasonOnce } from "@/components/ui/reason-once";
import { SkeletonPlot, SkeletonRows } from "@/components/ui/skeleton";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";
import { concentrationVerdict } from "@/lib/verdicts/portfolio";
import { asOfQuery } from "@/lib/xray/as-of";

type Allocation = components["schemas"]["Allocation"];
type AllocationNode = components["schemas"]["AllocationNode"];

/** Every holding leaf under a node, largest first. */
export function leaves(nodes: readonly AllocationNode[]): AllocationNode[] {
  const out: AllocationNode[] = [];
  const walk = (n: AllocationNode) => {
    if (n.level === "holding") out.push(n);
    for (const c of n.children ?? []) walk(c);
  };
  nodes.forEach(walk);
  return out.sort((a, b) => b.weight - a.weight);
}

/** Effective holdings on a 1 to 30 scale: under 5 is concentrated, 5 to 10 fairly, above 10 spread. */
const EFFECTIVE_ZONES: readonly GaugeZone[] = [
  { to: 5, tone: "loss", label: "Concentrated" },
  { to: 10, tone: "neutral", label: "Fairly concentrated" },
  { to: 30, tone: "gain", label: "Spread" },
];

function Bar({ value, max, muted }: { value: number; max: number; muted?: boolean }) {
  return (
    <span aria-hidden="true" className="block h-2 w-full rounded-full bg-chart-track">
      <span className={cn("block h-full rounded-full", muted ? "bg-chart-unknown" : "bg-chart-line")} style={{ width: `${max > 0 ? Math.min(100, (value / max) * 100) : 0}%` }} />
    </span>
  );
}

/** Sector rows that open into their industries and holdings: the sector, industry, holding tree as one list. */
function SectorTree({ tree }: { tree: readonly AllocationNode[] }) {
  const [open, setOpen] = useState<string | null>(tree[0]?.id ?? null);
  const max = Math.max(...tree.map((s) => s.weight), 0);
  return (
    <ul className="divide-y divide-border-soft">
      {tree.map((s) => {
        const expanded = open === s.id;
        return (
          <li key={s.id}>
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => setOpen(expanded ? null : s.id)}
              className="grid w-full grid-cols-[1rem_minmax(0,11rem)_1fr_3.5rem] items-center gap-3 py-2 text-left text-sm hover:text-text focus-visible:outline-2 focus-visible:outline-accent"
            >
              <ChevronRightIcon aria-hidden="true" className={cn("size-4 text-text-muted transition-transform motion-reduce:transition-none", expanded && "rotate-90")} />
              <span className={cn("truncate", s.unclassified ? "text-text-muted" : "font-medium")}>{s.label}</span>
              <Bar value={s.weight} max={max} muted={s.unclassified} />
              <span className="num text-right">{formatPercent(s.weight, { decimals: 1 })}</span>
            </button>
            {expanded ? (
              <div className="space-y-3 pb-3 pl-7">
                {(s.children ?? []).map((ind) => (
                  <div key={ind.id}>
                    <p className="flex justify-between gap-3 text-xs text-text-muted">
                      <span className="truncate">{ind.label}</span>
                      <span className="num">{formatPercent(ind.weight, { decimals: 1 })}</span>
                    </p>
                    <ul className="mt-1 flex flex-wrap gap-1.5">
                      {(ind.children ?? []).map((h) => (
                        // Issuers here include ones held only through funds, so they are not all company pages.
                        <li key={h.id} className="inline-flex items-baseline gap-1.5 rounded-control bg-surface-muted px-2 py-0.5 text-xs">
                          <span className="font-medium">{h.label}</span>
                          <span className="num text-text-muted">{formatPercent(h.weight, { decimals: 1 })}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Allocation at a glance (plan §5.3 Allocation): where the equity money sits by sector, opened
 * into industries and holdings, and how concentrated it is, ahead of the lens-by-lens detail.
 */
export function AllocationOverview({ asOf }: { asOf: string }) {
  const fetcher = useMemo(() => () => getBrowserApiClient().GET("/portfolio/xray/allocation", { params: { query: asOfQuery(asOf || undefined) } }), [asOf]);
  const { state } = useApiResource(fetcher);
  if (state.kind === "error") return null;
  if (state.kind === "loading") {
    return (
      <div className="grid items-start gap-5 lg:grid-cols-[3fr_2fr]">
        <ChartFrame title="Where the money sits">
          <SkeletonRows rows={6} />
        </ChartFrame>
        <ChartFrame title="How concentrated it is">
          <SkeletonPlot height={180} />
        </ChartFrame>
      </div>
    );
  }
  const a = state.data as Allocation;
  const holdings = leaves(a.tree);
  const l = a.lorenz;
  const verdict = concentrationVerdict(l.effective_n, l.holdings);
  return (
    <div className="grid items-start gap-5 lg:grid-cols-[3fr_2fr]">
      <ChartFrame
        title="Where the money sits"
        reading={a.equity_weight < 0.999 ? `Equity is ${formatPercent(a.equity_weight, { decimals: 0 })} of the portfolio; the tree shows that part.` : undefined}
        dataset="Holdings snapshot by sector, industry and holding"
        asOf={a.as_of}
      >
        {a.tree.length === 0 ? <LimitedState fixHref="/xray/inputs">{(a.reasons ?? [])[0] ?? "No classified equity holdings yet."}</LimitedState> : <SectorTree tree={a.tree} />}
      </ChartFrame>
      <ChartFrame
        title="How concentrated it is"
        reading={<VerdictLine verdict={verdict.tone}>{verdict.text}</VerdictLine>}
        dataset="Equity weights, largest first"
        asOf={a.as_of}
        learnHref="/xray/concentration"
      >
        {holdings.length < 2 ? (
          <LimitedState>{l.reason ?? "Needs at least two holdings."}</LimitedState>
        ) : (
          <div className="space-y-4">
            <LorenzChart weights={holdings.map((h) => h.weight)} names={holdings.map((h) => h.label)} height={180} />
            {l.effective_n !== null ? (
              <BandGauge
                value={Math.max(1, Math.min(30, l.effective_n))}
                min={1}
                max={30}
                zones={EFFECTIVE_ZONES}
                marker={`${l.effective_n.toFixed(1)} effective holdings`}
                format={(v) => v.toFixed(0)}
                label="Effective number of holdings"
              />
            ) : null}
            <dl className="grid grid-cols-3 gap-3 text-sm">
              <div>
                <dt className="text-xs text-text-muted">Holdings</dt>
                <dd className="num font-semibold">{l.holdings}</dd>
              </div>
              <div>
                <dt className="text-xs text-text-muted">Concentration index</dt>
                <dd className="num font-semibold">{l.hhi === null ? "—" : l.hhi.toFixed(3)}</dd>
              </div>
              <div>
                <dt className="text-xs text-text-muted">Largest holding</dt>
                <dd className="num font-semibold">{l.top_weight_share === null ? "—" : formatPercent(l.top_weight_share, { decimals: 0 })}</dd>
              </div>
            </dl>
          </div>
        )}
      </ChartFrame>
      <ReasonOnce className="lg:col-span-2" reasons={[...a.notes, ...(a.reasons ?? [])]} />
    </div>
  );
}
