"use client";

import { Bar, BarChart, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { HoldingContribution } from "@/lib/api/client";
import { formatMoney } from "@/lib/format";
import { shortTicker } from "@/lib/portfolio/ticker";

const AXIS_TICK = { fill: "var(--text-muted)", fontSize: 12 };

function ChartTooltip({
  active,
  payload,
  currency,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: HoldingContribution }>;
  currency: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="num rounded-md border border-border bg-surface-raised px-3 py-2 text-xs shadow-md">
      <span className="font-medium text-text">{row.name ?? shortTicker(row.ticker)}</span>:{" "}
      {formatMoney(row.contribution_base, currency, { signed: true })}
    </div>
  );
}

/**
 * Per-holding contribution to the period's return (§5.4): the one visualization here that
 * answers *why* the portfolio moved, not just *that* it moved or *how it's split*. Ranked
 * largest positive to largest negative (the API's own order); gain/loss colours, the same
 * charge as the figure itself (a positive contribution is a gain, a negative one a loss —
 * unlike drift or concentration, this metric does have a "good/bad" side).
 */
/** How many lifters and how many drags get their own bar; the rest fold into one "Other" bar. */
const EACH_SIDE = 6;

function shortName(h: HoldingContribution): string {
  const n = h.name ?? shortTicker(h.ticker);
  return n.length > 16 ? `${n.slice(0, 15).trimEnd()}…` : n;
}

/** The biggest lifters and drags with everything in between summed into one bar, largest first. */
export function contributionRows(holdings: readonly HoldingContribution[]): (HoldingContribution & { label: string })[] {
  const sorted = [...holdings].sort((a, b) => b.contribution_base - a.contribution_base);
  if (sorted.length <= EACH_SIDE * 2 + 1) return sorted.map((h) => ({ ...h, label: shortName(h) }));
  const top = sorted.slice(0, EACH_SIDE);
  const bottom = sorted.slice(-EACH_SIDE);
  const middle = sorted.slice(EACH_SIDE, -EACH_SIDE);
  const other = {
    ...middle[0],
    ticker: "__other__",
    name: `Other ${middle.length}`,
    contribution_base: middle.reduce((s, h) => s + h.contribution_base, 0),
    label: `Other ${middle.length}`,
  };
  return [...top.map((h) => ({ ...h, label: shortName(h) })), other, ...bottom.map((h) => ({ ...h, label: shortName(h) }))];
}

export function ContributionWaterfall({ holdings, currency, label }: { holdings: HoldingContribution[]; currency: string; label: string }) {
  if (holdings.length === 0) return null;
  const rows = contributionRows(holdings);
  const bound = Math.max(1, ...rows.map((r) => Math.abs(r.contribution_base))) * 1.15;
  return (
    <div role="img" aria-label={label} style={{ height: Math.max(120, rows.length * 28) }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }} barCategoryGap={8}>
          <XAxis
            type="number"
            domain={[-bound, bound]}
            ticks={[-bound / 1.15, 0, bound / 1.15]}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => formatMoney(v, currency, { decimals: 0 })}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={132}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            interval={0}
          />
          <ReferenceLine x={0} stroke="var(--border)" />
          <Tooltip cursor={{ fill: "var(--surface-muted)" }} content={<ChartTooltip currency={currency} />} />
          <Bar dataKey="contribution_base" radius={2} isAnimationActive={false} maxBarSize={16}>
            {rows.map((r) => (
              <Cell key={r.ticker} fill={r.ticker === "__other__" ? "var(--text-faint)" : r.contribution_base >= 0 ? "var(--gain)" : "var(--loss)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
