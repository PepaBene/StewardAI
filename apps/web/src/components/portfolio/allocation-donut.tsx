"use client";

import { useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import { formatMoney, formatPercent } from "@/lib/format";
import { categoricalFills } from "@/lib/portfolio/categorical";
import type { AllocationItem } from "@/components/portfolio/allocation-bars";

function DonutTooltip({
  active,
  payload,
  currency,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: AllocationItem }>;
  currency: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const item = payload[0]?.payload;
  if (!item) return null;
  return (
    <div className="num rounded-md border border-border bg-surface-raised px-3 py-2 text-xs shadow-md">
      <span className="font-medium text-text">{item.label}</span>: {formatMoney(item.value, currency, { decimals: 0 })} (
      {formatPercent(item.weight)})
    </div>
  );
}

/**
 * Allocation as a donut (visualization §5.2): the same `data.allocation` the stacked bar
 * already consumes, coloured by categoricalFills() so a slice is the exact same hue as the
 * stacked bar and the bar list — Copilot Money's "color literacy" principle. Sits beside
 * the stacked bar, not instead of it: a stacked bar reads exact adjacent weights better in
 * a line, a donut reads the overall shape faster at a glance. Clicking a slice isolates it
 * (dims the rest), following Empower's click-to-drill allocation view.
 */
export function AllocationDonut({ items, currency, label }: { items: AllocationItem[]; currency: string; label: string }) {
  const [isolated, setIsolated] = useState<string | null>(null);
  if (items.length === 0) return null;
  const fills = categoricalFills(items);
  const total = items.reduce((sum, i) => sum + i.value, 0);

  return (
    <div className="space-y-2">
      {/* The same allocation is already stated in text next to this (the bar list); this
          visual is a supplementary, click-to-isolate view, not the accessible record. */}
      <div aria-hidden="true" title={label} className="relative h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={items}
              dataKey="value"
              nameKey="label"
              innerRadius="60%"
              outerRadius="100%"
              paddingAngle={2}
              cornerRadius={3}
              stroke="var(--surface)"
              strokeWidth={2}
              isAnimationActive={false}
              onClick={(_, i) => setIsolated((prev) => (prev === items[i].key ? null : items[i].key))}
            >
              {items.map((item, i) => (
                <Cell
                  key={item.key}
                  fill={fills[i]}
                  opacity={isolated === null || isolated === item.key ? 1 : 0.25}
                  className="cursor-pointer outline-none"
                />
              ))}
            </Pie>
            <Tooltip content={<DonutTooltip currency={currency} />} />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="num text-lg font-semibold text-text">{formatMoney(total, currency, { decimals: 0 })}</span>
          <span className="text-xs text-text-muted">Total</span>
        </div>
      </div>
      {isolated ? (
        <p className="text-center text-xs text-text-muted">
          Showing {items.find((i) => i.key === isolated)?.label} —{" "}
          <button type="button" onClick={() => setIsolated(null)} className="text-accent hover:underline">
            clear
          </button>
        </p>
      ) : null}
    </div>
  );
}
