"use client";

import { ResponsiveContainer, Tooltip, Treemap } from "recharts";

import type { Holding } from "@/lib/api/client";
import { formatMoney, formatPercent } from "@/lib/format";
import { treemapShadeOpacity } from "@/lib/portfolio/treemap";

type Node = {
  name: string;
  ticker: string;
  size: number;
  pplPct: number | null;
  ppl: number;
  currency: string;
};

type CellProps = {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  payload?: Node;
};

function TreemapCell({ x = 0, y = 0, width = 0, height = 0, payload }: CellProps) {
  if (!payload) return null;
  const gain = (payload.pplPct ?? 0) >= 0;
  const showLabel = width > 56 && height > 28;
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        fill={gain ? "var(--gain)" : "var(--loss)"}
        fillOpacity={treemapShadeOpacity(payload.pplPct)}
        stroke="var(--surface)"
        strokeWidth={2}
      />
      {showLabel ? (
        <text x={x + 6} y={y + 16} fill="var(--text)" fontSize={12} fontWeight={500}>
          {payload.ticker}
        </text>
      ) : null}
      {showLabel && height > 44 ? (
        <text x={x + 6} y={y + 32} fill="var(--text)" fillOpacity={0.8} fontSize={11}>
          {formatPercent(payload.pplPct, { signed: true })}
        </text>
      ) : null}
    </g>
  );
}

function TreemapTooltip({ active, payload }: { active?: boolean; payload?: ReadonlyArray<{ payload?: Node }> }) {
  if (!active || !payload || payload.length === 0) return null;
  const node = payload[0]?.payload;
  if (!node) return null;
  return (
    <div className="num rounded-md border border-border bg-surface-raised px-3 py-2 text-xs shadow-md">
      <p className="font-medium text-text">{node.name}</p>
      <p className="text-text-muted">
        {formatMoney(node.size, node.currency, { decimals: 0 })} · {formatMoney(node.ppl, node.currency, { signed: true })} (
        {formatPercent(node.pplPct, { signed: true })})
      </p>
    </div>
  );
}

/**
 * The portfolio as a size/colour map (§5.5): tile area is position value, tile colour is
 * unrealised P/L (gain/loss, shaded by the size of the move) — winners and losers at a
 * glance, trading exact figures for shape. An alternate view beside the holdings table,
 * never a replacement: the table is still what states "what exactly is this worth."
 */
export function PortfolioTreemap({ holdings, currency }: { holdings: Holding[]; currency: string }) {
  const data: Node[] = holdings
    .filter((h) => h.value_base > 0)
    .map((h) => ({
      name: h.name ?? h.ticker,
      ticker: h.ticker,
      size: h.value_base,
      pplPct: h.ppl_pct,
      ppl: h.ppl,
      currency: h.account_currency,
    }));
  if (data.length === 0) return null;
  return (
    <div role="img" aria-label={`Portfolio by value in ${currency}, shaded by unrealised P/L`} className="h-[22rem] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <Treemap
          data={data}
          dataKey="size"
          isAnimationActive={false}
          content={<TreemapCell />}
        >
          <Tooltip content={<TreemapTooltip />} />
        </Treemap>
      </ResponsiveContainer>
    </div>
  );
}
