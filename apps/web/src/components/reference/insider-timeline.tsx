"use client";

import { SrTable } from "@/components/charts";
import { formatCompact } from "@/lib/charts/format";
import { linearScale } from "@/lib/charts/scale";
import type { components } from "@/lib/api/schema";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/*
 * Insider timeline (insight I13.6): each counted open-market trade as a lollipop at its date
 * (up for a purchase, down for a sale, stem length the trade's value), and beneath it a strip of
 * monthly bought and sold value on the same time axis, so a cluster of sales reads at a glance.
 * Purchases and sales also differ by marker shape, never by colour alone.
 */

type Activity = components["schemas"]["InsiderActivityOut"];

const W = 640;
const PAD_L = 8;
const PAD_R = 8;

const num = (v: number | string | null | undefined): number | null => {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const dayMs = (d: string) => new Date(`${d}T00:00:00Z`).getTime();

export function InsiderTimeline({ data }: { data: Activity }) {
  const since = dayMs(data.net_discretionary.since);
  const until = dayMs(data.net_discretionary.until);
  const trades = data.transactions
    .filter((t) => t.counts_toward_net_discretionary && t.transaction_date && num(t.value) !== null)
    .map((t) => ({
      day: t.transaction_date as string,
      buy: t.economic_class === "open_market_buy",
      value: num(t.value) as number,
      who: t.owner_name ?? t.reporting_owner_cik,
      role: t.insider_role,
    }));
  const months = data.monthly ?? [];
  if (trades.length === 0 && months.every((m) => num(m.bought_value) === 0 && num(m.sold_value) === 0)) return null;
  const x = linearScale([since, Math.max(until, since + 1)], [PAD_L, W - PAD_R]);
  const maxTrade = Math.max(1, ...trades.map((t) => Math.abs(t.value)));
  const H = 130;
  const mid = H / 2;
  const y = linearScale([0, maxTrade], [0, mid - 12]);
  const maxMonth = Math.max(1, ...months.map((m) => Math.max(num(m.bought_value) ?? 0, num(m.sold_value) ?? 0)));
  const SH = 56;
  const sy = linearScale([0, maxMonth], [0, SH / 2 - 4]);
  const monthW = months.length > 0 ? (W - PAD_L - PAD_R) / months.length : 0;
  return (
    <div className="space-y-1.5">
      <h3 className="text-sm font-semibold">Each trade, and the value traded by month</h3>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Insider trades over time: ${trades.filter((t) => t.buy).length} purchases and ${trades.filter((t) => !t.buy).length} sales`} className="block h-auto w-full overflow-visible">
        <line x1={PAD_L} x2={W - PAD_R} y1={mid} y2={mid} className="stroke-border" strokeWidth={1} />
        {trades.map((t, i) => {
          const cx = x(dayMs(t.day));
          const len = Math.max(2, y(Math.abs(t.value)));
          const cy = t.buy ? mid - len : mid + len;
          return (
            <g key={`${t.day}-${i}`}>
              <line x1={cx} x2={cx} y1={mid} y2={cy} className={cn(t.buy ? "stroke-gain" : "stroke-loss")} strokeWidth={1.5} />
              {t.buy ? (
                <circle cx={cx} cy={cy} r={4} className="fill-gain stroke-surface" strokeWidth={1.5} />
              ) : (
                <rect x={cx - 4} y={cy - 4} width={8} height={8} className="fill-loss stroke-surface" strokeWidth={1.5} />
              )}
              <title>{`${formatDate(t.day)}: ${t.who} (${t.role}) ${t.buy ? "bought" : "sold"} ${formatCompact(t.value)}`}</title>
            </g>
          );
        })}
        <text x={PAD_L} y={10} className="fill-text-muted text-xs">Bought</text>
        <text x={PAD_L} y={H - 2} className="fill-text-muted text-xs">Sold</text>
      </svg>
      {months.length > 0 ? (
        <svg viewBox={`0 0 ${W} ${SH + 14}`} role="img" aria-label="Value bought and sold per month" className="block h-auto w-full">
          <line x1={PAD_L} x2={W - PAD_R} y1={SH / 2} y2={SH / 2} className="stroke-border" strokeWidth={1} />
          {months.map((m, i) => {
            const bx = PAD_L + i * monthW + 1;
            const bw = Math.max(1, monthW - 2);
            const b = sy(num(m.bought_value) ?? 0);
            const s = sy(num(m.sold_value) ?? 0);
            return (
              <g key={m.month}>
                {b > 0 ? <rect x={bx} y={SH / 2 - b} width={bw} height={b} className="fill-gain" rx={1} /> : null}
                {s > 0 ? <rect x={bx} y={SH / 2} width={bw} height={s} className="fill-loss" rx={1} /> : null}
                {i % 3 === 0 ? (
                  <text x={bx + bw / 2} y={SH + 11} textAnchor="middle" className="fill-text-muted text-xs">
                    {m.month.slice(2, 7)}
                  </text>
                ) : null}
                <title>{`${m.month.slice(0, 7)}: bought ${formatCompact(num(m.bought_value) ?? 0)}, sold ${formatCompact(num(m.sold_value) ?? 0)}${m.value_complete ? "" : " (a trade without a price is not valued)"}`}</title>
              </g>
            );
          })}
        </svg>
      ) : null}
      <SrTable
        caption="Value bought and sold by insiders per month, at reported prices"
        columns={["Month", "Bought", "Sold", "Net", "Purchases", "Sales"]}
        rows={months.map((m) => [m.month.slice(0, 7), formatCompact(num(m.bought_value) ?? 0), formatCompact(num(m.sold_value) ?? 0), formatCompact(num(m.net_value) ?? 0), m.buys, m.sells])}
      />
      <p className="text-xs text-text-muted">
        Circles are purchases, squares are sales; stem length is the trade&apos;s value at the reported price. The strip below sums the value per month. Only open-market discretionary trades are drawn.
      </p>
    </div>
  );
}
