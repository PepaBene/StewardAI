"use client";

import { Bar, BarChart, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatPp } from "@/lib/format";

export type DriftChartRow = { key: string; label: string; drift: number };

const AXIS_TICK = { fill: "var(--text-muted)", fontSize: 12 };

function ChartTooltip({ active, payload }: { active?: boolean; payload?: ReadonlyArray<{ payload?: DriftChartRow }> }) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  return (
    <div className="num rounded-md border border-border bg-surface-raised px-3 py-2 text-xs shadow-md">
      <span className="font-medium text-text">{row.label}</span>: {formatPp(row.drift)}
    </div>
  );
}

/**
 * Drift from target as a standalone diverging bar chart (visualization C): one bar per
 * position, warm (diverging-over) to the right of a zero baseline for overweight, cool
 * (diverging-under) to the left for underweight — a delta-to-target question, which
 * diverging bars answer directly, at a glance, before reading a single row of the table.
 */
export function DriftChart({ rows, label }: { rows: DriftChartRow[]; label: string }) {
  if (rows.length === 0) return null;
  const bound = Math.max(0.02, ...rows.map((r) => Math.abs(r.drift))) * 1.15;
  return (
    <div role="img" aria-label={label} style={{ height: Math.max(80, rows.length * 32) }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }} barCategoryGap={8}>
          <XAxis
            type="number"
            domain={[-bound, bound]}
            ticks={[-bound, -bound / 2, 0, bound / 2, bound]}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => formatPp(v)}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={72}
            tick={{ ...AXIS_TICK, fontFamily: "var(--font-mono)" }}
            tickLine={false}
            axisLine={false}
          />
          <ReferenceLine x={0} stroke="var(--border)" />
          <Tooltip cursor={{ fill: "var(--surface-muted)" }} content={<ChartTooltip />} />
          <Bar dataKey="drift" radius={2} isAnimationActive={false} maxBarSize={20}>
            {rows.map((r) => (
              <Cell key={r.key} fill={r.drift >= 0 ? "var(--diverging-over)" : "var(--diverging-under)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
