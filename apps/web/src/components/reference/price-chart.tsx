"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDate } from "@/lib/format";
import { formatIndex, type ChartPoint } from "@/lib/reference/prices";

/*
 * The holding and its benchmark, both rebased to 100 on one axis (never two y-scales).
 * Colours come from tokens; the benchmark is also dashed so the two lines never differ by
 * colour alone. The legend names both series; a text summary follows the chart.
 */

const AXIS_TICK = { fill: "var(--text-muted)", fontSize: 12 };

function shortDate(iso: string) {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString(undefined, { month: "short", year: "2-digit" });
}

function ChartTooltip({
  active,
  payload,
  holdingLabel,
  benchmarkLabel,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: unknown }>;
  holdingLabel: string;
  benchmarkLabel: string | null;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0]?.payload as ChartPoint | undefined;
  if (!point) return null;
  return (
    <div className="rounded-md border border-border bg-surface-raised px-3 py-2 text-xs shadow-md">
      <p className="font-medium text-text">{formatDate(point.date)}</p>
      <table className="num mt-1">
        <tbody>
          <tr>
            <th scope="row" className="pr-3 text-left font-normal text-text-muted">
              <span aria-hidden="true" className="mr-1.5 inline-block h-0.5 w-3 bg-chart-line align-middle" />
              {holdingLabel}
            </th>
            <td className="pr-2 text-right text-text">{formatIndex(point.holding)}</td>
            <td className="text-right text-text-muted">close {point.holdingClose ?? "—"}</td>
          </tr>
          {benchmarkLabel ? (
            <tr>
              <th scope="row" className="pr-3 text-left font-normal text-text-muted">
                <span
                  aria-hidden="true"
                  className="mr-1.5 inline-block w-3 border-t-2 border-dashed border-chart-benchmark align-middle"
                />
                {benchmarkLabel}
              </th>
              <td className="pr-2 text-right text-text">{formatIndex(point.benchmark)}</td>
              <td className="text-right text-text-muted">close {point.benchmarkClose ?? "—"}</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

export function PriceChart({
  points,
  holdingLabel,
  benchmarkLabel,
  label,
}: {
  points: ChartPoint[];
  holdingLabel: string;
  /** Null when the benchmark is not drawn. */
  benchmarkLabel: string | null;
  /** Accessible description of the chart. */
  label: string;
}) {
  return (
    <div role="img" aria-label={label} className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--border)" strokeDasharray="2 4" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={shortDate}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: "var(--border)" }}
            minTickGap={48}
          />
          <YAxis
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={48}
            domain={["auto", "auto"]}
            tickFormatter={(v: number) => formatIndex(v).replace(/\.0$/, "")}
          />
          <ReferenceLine y={100} stroke="var(--border)" />
          <Tooltip
            cursor={{ stroke: "var(--text-muted)", strokeWidth: 1 }}
            content={({ active, payload }) => (
              <ChartTooltip
                active={active}
                payload={payload}
                holdingLabel={holdingLabel}
                benchmarkLabel={benchmarkLabel}
              />
            )}
          />
          <Legend
            verticalAlign="top"
            align="left"
            height={28}
            iconType="plainline"
            formatter={(value) => <span className="text-xs text-text">{value}</span>}
          />
          <Line
            type="linear"
            dataKey="holding"
            name={holdingLabel}
            stroke="var(--chart-line)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, stroke: "var(--surface-raised)", strokeWidth: 2 }}
            isAnimationActive={false}
          />
          {benchmarkLabel ? (
            <Line
              type="linear"
              dataKey="benchmark"
              name={benchmarkLabel}
              stroke="var(--chart-benchmark)"
              strokeWidth={2}
              strokeDasharray="6 4"
              dot={false}
              connectNulls
              activeDot={{ r: 4, stroke: "var(--surface-raised)", strokeWidth: 2 }}
              isAnimationActive={false}
            />
          ) : null}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
