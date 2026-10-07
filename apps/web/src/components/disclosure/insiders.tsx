"use client";

import { useState } from "react";

import { callApi, DecisionForm } from "@/app/(app)/relationships/decision-form";
import { DivergingBar } from "@/components/charts";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDay } from "@/lib/disclosure/model";
import {
  CLASS_META,
  classOf,
  classesPresent,
  formatQty,
  formatUsd,
  netBars,
  netFromTransactions,
  num,
  PLAN_LABEL,
  ROLE_LABEL,
  stripEvents,
  WINDOWS,
  type ClassKey,
  type InsiderActivity,
  type InsiderTx,
  type Mark,
  type Window,
} from "@/lib/disclosure/insiders";
import { cn } from "@/lib/utils";

/** One mark per class, drawn at a point. Shape and fill carry the class; colour never does alone. */
function MarkShape({ mark, filled, x, y, r = 6 }: { mark: Mark; filled: boolean; x: number; y: number; r?: number }) {
  const common = { strokeWidth: 2, className: cn("stroke-chart-line", filled ? "fill-chart-line" : "fill-surface-raised") };
  switch (mark) {
    case "triangle-up":
      return <polygon points={`${x},${y - r} ${x + r},${y + r * 0.8} ${x - r},${y + r * 0.8}`} {...common} />;
    case "triangle-down":
      return <polygon points={`${x},${y + r} ${x + r},${y - r * 0.8} ${x - r},${y - r * 0.8}`} {...common} />;
    case "diamond":
      return <polygon points={`${x},${y - r} ${x + r},${y} ${x},${y + r} ${x - r},${y}`} {...common} />;
    case "circle":
      return <circle cx={x} cy={y} r={r * 0.85} {...common} />;
    case "square":
      return <rect x={x - r * 0.8} y={y - r * 0.8} width={r * 1.6} height={r * 1.6} {...common} />;
    case "plus":
      return <path d={`M ${x - r} ${y} H ${x + r} M ${x} ${y - r} V ${y + r}`} fill="none" strokeWidth={2.5} className="stroke-chart-line" />;
    default:
      return <path d={`M ${x - r * 0.8} ${y - r * 0.8} L ${x + r * 0.8} ${y + r * 0.8} M ${x + r * 0.8} ${y - r * 0.8} L ${x - r * 0.8} ${y + r * 0.8}`} fill="none" strokeWidth={2.5} className="stroke-text-muted" />;
  }
}

const W = 760;
const LANE = 30;
const PAD = { l: 168, r: 18, t: 16, b: 30 };

/**
 * Every filed line on one date axis, a row per class. A thin stem runs from the trade date to the
 * date the filing was made, so the reporting lag is visible. Hollow stems were filed after the date
 * the page is replaying.
 */
export function EventStrip({ txs, cutoffMs }: { txs: readonly InsiderTx[]; cutoffMs: number }) {
  const { events, from, to } = stripEvents(txs, cutoffMs);
  const lanes = classesPresent(txs);
  if (events.length === 0) return <p className="text-sm text-text-muted">No insider filings are known for this company at this date.</p>;
  const H = PAD.t + lanes.length * LANE + PAD.b;
  const x = (f: number) => PAD.l + f * (W - PAD.l - PAD.r);
  const y = (c: ClassKey) => PAD.t + lanes.indexOf(c) * LANE + LANE / 2;
  const months: { at: number; label: string }[] = [];
  for (let d = new Date(from); d.getTime() <= to; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1))) {
    const first = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
    if (first >= from) months.push({ at: (first - from) / Math.max(to - from, 1), label: new Intl.DateTimeFormat("en-GB", { month: "short", year: "2-digit", timeZone: "UTC" }).format(first) });
  }
  return (
    <figure className="space-y-2">
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full max-w-4xl" role="img" aria-label="Insider transactions by class over time, each with the date it was filed">
        {lanes.map((c) => (
          <g key={c}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(c)} y2={y(c)} className="stroke-chart-grid" strokeDasharray="2 4" />
            <text x={PAD.l - 10} y={y(c)} textAnchor="end" dominantBaseline="middle" className={cn("text-[12px]", CLASS_META[c].discretionary ? "fill-text font-medium" : "fill-text-muted")}>
              {CLASS_META[c].label}
            </text>
          </g>
        ))}
        {months.map((m) => (
          <text key={m.label} x={x(m.at)} y={H - 10} className="num fill-text-muted text-[11px]">{m.label}</text>
        ))}
        <line x1={x((cutoffMs - from) / Math.max(to - from, 1))} x2={x((cutoffMs - from) / Math.max(to - from, 1))} y1={PAD.t - 6} y2={H - PAD.b} className="stroke-text" strokeWidth={1.5} />
        {events.map((e) => (
          <g key={e.id} opacity={e.known ? 1 : 0.55}>
            <title>{e.label}</title>
            <line x1={x(e.x)} x2={x(e.xFiled)} y1={y(e.cls)} y2={y(e.cls)} strokeWidth={2} strokeDasharray={e.known ? undefined : "3 3"} className="stroke-chart-line" />
            <rect x={x(e.xFiled) - 1.5} y={y(e.cls) - 5} width={3} height={10} className="fill-chart-line" />
            <MarkShape mark={CLASS_META[e.cls].mark} filled={CLASS_META[e.cls].filled} x={x(e.x)} y={y(e.cls)} />
          </g>
        ))}
      </svg>
      <figcaption className="max-w-[72ch] text-xs text-text-muted">
        The mark is the trade date; the bar at the end of its stem is the filing date. Only the first two rows are discretionary market trades. The vertical line is the date being replayed.
      </figcaption>
    </figure>
  );
}

// ------------------------------------------------------------------------------- net by role
function usdAxis(v: number) {
  return formatUsd(v);
}

export function NetPanel({ view, cutoffMs }: { view: InsiderActivity; cutoffMs: number }) {
  const [window, setWindow] = useState<Window>(90);
  const rows = netFromTransactions(view.transactions, cutoffMs);
  const all = rows.find((r) => r.role === "all" && r.window === window)!;
  const bars = netBars(rows, window);
  return (
    <div className="space-y-4">
      <div role="group" aria-label="Look-back window" className="flex gap-1">
        {WINDOWS.map((w) => (
          <button
            key={w}
            type="button"
            aria-pressed={w === window}
            onClick={() => setWindow(w)}
            className={cn("num h-8 border px-3 text-sm focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none", w === window ? "border-accent bg-accent text-accent-contrast" : "border-border bg-surface-raised hover:bg-surface-muted")}
          >
            {w} days
          </button>
        ))}
      </div>
      {all.trades === 0 ? (
        <p className="text-sm text-text-muted">No discretionary open-market trades fall in the {window} days before this date.</p>
      ) : (
        <DivergingBar rows={bars} colorMode="direction" formatValue={usdAxis} label={`Net open-market purchases less sales, last ${window} days`} />
      )}
      <p className="text-sm">
        <strong className="num">{all.insiders}</strong> {all.insiders === 1 ? "person" : "people"} made <strong className="num">{all.trades}</strong> discretionary {all.trades === 1 ? "trade" : "trades"}: bought <span className="num">{formatUsd(all.buyValue)}</span>, sold <span className="num">{formatUsd(all.sellValue)}</span>.
      </p>
      <table className="w-full max-w-3xl border-collapse text-sm">
        <caption className="sr-only">Net open-market value by role over the last {window} days, with trades kept out of the net</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Role</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Bought</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Sold</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Net</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">People</th>
          </tr>
        </thead>
        <tbody>
          {rows.filter((r) => r.window === window && (r.trades > 0 || r.role === "all")).map((r) => (
            <tr key={r.role} className={cn("border-b border-border-soft", r.role === "all" && "font-medium")}>
              <th scope="row" className="py-1.5 pr-3 text-left font-medium">{ROLE_LABEL[r.role]}</th>
              <td className="num py-1.5 pr-3 text-right">{formatUsd(r.buyValue)}</td>
              <td className="num py-1.5 pr-3 text-right">{formatUsd(r.sellValue)}</td>
              <td className="num py-1.5 pr-3 text-right">{formatUsd(r.netValue)}</td>
              <td className="num py-1.5 pr-3 text-right">{r.insiders}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="max-w-[72ch] space-y-0.5 text-xs text-text-muted">
        <li>Counted: open-market purchases and sales on shares held, with a price above zero. Awards, exercises, tax withholding and gifts are not.</li>
        {all.planExcluded > 0 ? <li>{all.planExcluded} {all.planExcluded === 1 ? "trade was" : "trades were"} made under a Rule 10b5-1 plan and {all.planExcluded === 1 ? "is" : "are"} left out of the net.</li> : null}
        {all.bundled > 0 ? <li>{all.bundled} {all.bundled === 1 ? "sale was" : "sales were"} reported on the same day as an option exercise and {all.bundled === 1 ? "is" : "are"} left out of the net.</li> : null}
        {all.planUnknown > 0 ? <li>{all.planUnknown} counted {all.planUnknown === 1 ? "trade has" : "trades have"} no plan indicator in the filing, so {all.planUnknown === 1 ? "its" : "their"} plan status is not known.</li> : null}
        <li>This shows what was filed. It does not say why anyone traded.</li>
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------- evidence table
function ClassCell({ t }: { t: InsiderTx }) {
  const c = classOf(t);
  const meta = CLASS_META[c];
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width="16" height="16" viewBox="-8 -8 16 16" aria-hidden="true" className="shrink-0">
        <MarkShape mark={meta.mark} filled={meta.filled} x={0} y={0} r={5} />
      </svg>
      <span>{meta.label}</span>
    </span>
  );
}

export function whyNotCounted(t: InsiderTx): string | null {
  if (t.countable) return null;
  if (t.bundled) return "Bundled with a same-day exercise";
  if (t.plan === "plan") return "Under a 10b5-1 plan";
  if (!CLASS_META[classOf(t)].discretionary) return "Not an open-market trade";
  return "Not counted: missing price, shares or class";
}

const CLASS_OPTIONS = (Object.keys(CLASS_META) as ClassKey[])
  .filter((c) => c !== "unclassified")
  .map((c) => ({ value: c, label: `Reclassify as: ${CLASS_META[c].label}` }));

export function InsiderTable({ txs, editable, onChanged }: { txs: readonly InsiderTx[]; editable: boolean; onChanged: () => void }) {
  if (txs.length === 0) return <p className="text-sm text-text-muted">No transactions to list.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[920px] border-collapse text-sm">
        <caption className="sr-only">Insider transactions with dates, amounts, holdings after the trade and caveats</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Traded / filed</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Insider</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">What was reported</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Shares</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Price</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Held after</th>
            <th scope="col" className="py-1.5 font-medium">In the net, and caveats</th>
          </tr>
        </thead>
        <tbody>
          {txs.map((t) => {
            const out = whyNotCounted(t);
            const pc = num(t.position_change_percent);
            return (
              <tr key={`${t.accession}-${t.row_index}`} className="border-b border-border-soft align-top">
                <td className="num py-1.5 pr-3 whitespace-nowrap">
                  {t.tx_date ? formatDay(t.tx_date) : "No trade date"}
                  <span className="block text-xs text-text-muted">filed {formatDay(t.filed)}</span>
                </td>
                <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                  {t.insider}
                  <span className="block text-xs font-normal text-text-muted">{t.role.replaceAll("_", " ")}{t.direct_indirect === "I" ? ", held indirectly" : ""}</span>
                </th>
                <td className="py-1.5 pr-3">
                  <ClassCell t={t} />
                  {t.override_reason ? <span className="block text-xs text-text-muted">Reviewer changed this from “{t.classifier_class ?? "no class"}”: {t.override_reason}</span> : null}
                  {editable ? (
                    <OverrideForm t={t} onChanged={onChanged} />
                  ) : null}
                </td>
                <td className="num py-1.5 pr-3 text-right">{formatQty(t.shares)}</td>
                <td className="num py-1.5 pr-3 text-right">{t.price == null ? "—" : `$${formatQty(t.price)}`}</td>
                <td className="num py-1.5 pr-3 text-right">
                  {formatQty(t.shares_after)}
                  {pc != null ? <span className="block text-xs text-text-muted">{pc >= 0 ? "+" : "−"}{Math.abs(pc).toFixed(1)}% of position</span> : null}
                </td>
                <td className="py-1.5">
                  <span className={cn(out ? "text-text-muted" : "font-medium")}>{out ?? "Counted"}</span>
                  <span className="block text-xs text-text-muted">{PLAN_LABEL[t.plan] ?? t.plan}</span>
                  {t.caveats.filter((c) => c !== "Reported under a Rule 10b5-1 trading plan.").map((c) => (
                    <span key={c} className="block text-xs text-text-muted">{c}</span>
                  ))}
                  {t.source_url ? (
                    <a href={t.source_url} target="_blank" rel="noreferrer" className="text-xs text-accent-ink hover:underline">Open the filing</a>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function OverrideForm({ t, onChanged }: { t: InsiderTx; onChanged: () => void }) {
  return (
    <DecisionForm
      summary="Change the class"
      options={CLASS_OPTIONS}
      onSubmit={async (value, reason) => {
        const r = await callApi(
          () => getBrowserApiClient().POST("/disclosures/insiders/override", { body: { accession: t.accession, row_index: t.row_index, new_class: value, reason } }),
          "Saved. The original class stays on record.",
        );
        if (r.ok) onChanged();
        return r;
      }}
    />
  );
}

export function BundleList({ view }: { view: InsiderActivity }) {
  if (view.bundles.length === 0) return <p className="text-sm text-text-muted">No option exercise was followed by a same-day sale.</p>;
  return (
    <table className="w-full max-w-3xl border-collapse text-sm">
      <caption className="sr-only">Option exercises followed by a sale on the same day</caption>
      <thead>
        <tr className="border-b border-border text-left">
          <th scope="col" className="py-1.5 pr-3 font-medium">Day</th>
          <th scope="col" className="py-1.5 pr-3 text-right font-medium">Exercised</th>
          <th scope="col" className="py-1.5 pr-3 text-right font-medium">Sold</th>
          <th scope="col" className="py-1.5 pr-3 text-right font-medium">Proceeds</th>
          <th scope="col" className="py-1.5 text-right font-medium">Kept</th>
        </tr>
      </thead>
      <tbody>
        {view.bundles.map((b) => (
          <tr key={b.day} className="border-b border-border-soft">
            <th scope="row" className="num py-1.5 pr-3 text-left font-medium">{formatDay(b.day)}</th>
            <td className="num py-1.5 pr-3 text-right">{formatQty(b.exercised)}</td>
            <td className="num py-1.5 pr-3 text-right">{formatQty(b.sold)}</td>
            <td className="num py-1.5 pr-3 text-right">{formatUsd(b.proceeds)}</td>
            <td className="num py-1.5 text-right">{formatQty(b.retained)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
