"use client";

import {
  ArrowDownIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  ArrowUpDownIcon,
  ArrowUpIcon,
  Columns3Icon,
  GripVerticalIcon,
  NotebookPenIcon,
  XIcon,
} from "lucide-react";
import { Fragment, useMemo, useState, type ReactNode } from "react";

import { HBar, Sparkline } from "@/components/charts";
import { ScoreMark } from "@/components/insight/score-mark";
import { StepSparkline } from "@/components/insight/step-line";
import { BandBadge } from "@/components/portfolio/band-badge";
import { FundExpandButton, FundLookThroughRow } from "@/components/portfolio/fund-look-through-row";
import { useLookThrough } from "@/components/portfolio/look-through-exposure";
import { Blueprint } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import type { Holding, HoldingsResponse } from "@/lib/api/client";
import { formatCompact } from "@/lib/charts/format";
import { formatDate, formatMoney, formatPercent, formatPp, formatPrice, formatQuantity, gainLossTone } from "@/lib/format";
import { petalsFromMap } from "@/lib/insight/insight";
import {
  COLUMN_IDS,
  COLUMNS,
  MAX_CUSTOM,
  PRESETS,
  dropColumn,
  insightNumber,
  missingReason,
  moveColumn,
  nextSortState,
  priceWithFair,
  sortRows,
  toNum,
  toggleColumn,
  type ColumnGroup,
  type ColumnId,
  type HoldingInsight,
  type PresetKey,
  type SortState,
} from "@/lib/portfolio/holding-columns";
import { displayTicker, investedShare, securityClass, unrealisedTotal, weightScale, type HoldingPrices } from "@/lib/portfolio/holdings-view";
import { fundsByTicker } from "@/lib/portfolio/look-through";
import { dayMove } from "@/lib/prices/intraday";
import { cn } from "@/lib/utils";

import { LastEventLine } from "./holding-drawer";

const TONE_CLASS = { gain: "text-gain", loss: "text-loss", flat: "" };

/** "▲ +0.28%": a signed change with an arrow, so direction never rests on colour alone. */
export function Change({ value, digits = 2, suffix }: { value: number | null | undefined; digits?: number; suffix?: ReactNode }) {
  if (typeof value !== "number" || !Number.isFinite(value)) return <span className="text-text-muted">—</span>;
  const tone = gainLossTone(value);
  return (
    <span className={cn("num", TONE_CLASS[tone])}>
      {tone === "gain" ? "▲ " : tone === "loss" ? "▼ " : ""}
      {formatPercent(value, { decimals: digits, signed: true })}
      {suffix}
    </span>
  );
}

function Missing({ why }: { why: string | null }) {
  return (
    <span className="text-text-faint" title={why ?? undefined}>
      —{why ? <span className="sr-only"> ({why})</span> : null}
    </span>
  );
}

export type CellContext = {
  h: Holding;
  row: HoldingInsight | undefined;
  prices: HoldingPrices | undefined;
  baseCcy: string;
  scale: number;
  onOpen: (tab?: "overview" | "notes") => void;
  expand?: { open: boolean; onToggle: () => void; controls: string };
};

/** One cell's content for a column; the table and the mobile cards share it. */
export function cellContent(id: ColumnId, x: CellContext): ReactNode {
  const { h, row } = x;
  const def = COLUMNS[id];
  const short = displayTicker(h.ticker);
  const move = dayMove(x.prices, (d) => formatDate(d));
  switch (id) {
    case "holding":
      return null; // rendered by the row header
    case "quantity":
      return formatQuantity(h.quantity);
    case "avg_price":
      return <span className="text-text-muted">{formatPrice(h.avg_price, h.instrument_currency)}</span>;
    case "price":
      return (
        <>
          {formatPrice(h.current_price, h.instrument_currency)}
          <span className="block text-[11px]" title={move.title}>
            {move.value === null ? <span className="text-text-muted">—</span> : <Change value={move.value} />}
            {move.kind === "intraday" && move.stamp ? (
              <span className="ml-1 font-mono text-[10px] text-text-faint">
                <span className="sr-only">intraday, </span>
                {move.stamp}
              </span>
            ) : null}
          </span>
        </>
      );
    case "day_move":
      return <Change value={move.value} />;
    case "spark": {
      const bars = x.prices?.bars ?? [];
      return bars.length > 1 ? (
        <Sparkline values={bars.map((b) => b.close)} area width={100} height={30} className="h-[30px] w-full" label={`${short}, ${bars.length} closes`} />
      ) : (
        <span className="block text-center text-xs text-text-muted">—</span>
      );
    }
    case "value":
      return (
        <span className="font-semibold">
          {formatMoney(h.value_base, x.baseCcy, { decimals: 0 })}
          {h.account_currency !== x.baseCcy || h.instrument_currency !== x.baseCcy ? (
            <span className="block text-xs font-normal text-text-muted" title="Trading212's own conversion">
              T212 {formatMoney(h.value_account, h.account_currency)}
            </span>
          ) : null}
        </span>
      );
    case "weight":
      return (
        <>
          <span className="num mb-1 flex justify-between gap-2 text-xs">
            <span>{formatPercent(h.weight)}</span>
            <span className="text-text-muted">{h.target_weight !== null ? `target ${formatPercent(h.target_weight)}` : "no target"}</span>
          </span>
          <HBar value={h.weight} max={x.scale} target={h.target_weight} size="sm" label={`${short} weight`} className="h-1.5" />
        </>
      );
    case "drift":
      return (
        <span className="inline-flex flex-col items-end gap-1">
          {h.drift_status !== "no_target" ? <span className="num text-[13px]">{formatPp(h.drift)}</span> : null}
          <BandBadge status={h.drift_status} />
        </span>
      );
    case "ppl":
      return (
        <span className={TONE_CLASS[gainLossTone(h.ppl)]}>
          <span className="font-semibold">{formatMoney(h.ppl, h.account_currency, { decimals: 0, signed: true })}</span>
          <span className="block text-xs">{formatPercent(h.ppl_pct, { signed: true })}</span>
        </span>
      );
    case "last_event":
      return <LastEventLine row={row} compact />;
    case "thesis":
      return row?.thesis ? (
        <span className="line-clamp-2 max-w-[22rem] text-xs" title={row.thesis}>
          {row.thesis}
        </span>
      ) : (
        <span className="text-xs text-text-faint">No thesis</span>
      );
    case "fair_vs_price": {
      const up = toNum(row?.fair_upside);
      if (up === null) return <Missing why={missingReason(id, row)} />;
      return (
        <span className={cn(up < 0 ? "text-loss" : "text-gain")} title={row?.fair_basis ?? undefined}>
          {up < 0 ? `${formatPercent(-up, { decimals: 0 })} above` : `${formatPercent(up, { decimals: 0 })} below`}
          {row?.fair_stale ? <span className="block text-[11px] text-text-muted">stale</span> : null}
        </span>
      );
    }
    case "fair_value": {
      const v = toNum(row?.fair_value);
      return v === null ? <Missing why={missingReason(id, row)} /> : formatPrice(v, h.instrument_currency);
    }
    case "fair_spark": {
      const rows = priceWithFair(x.prices?.bars ?? [], row?.fair_history ?? []);
      return <StepSparkline rows={rows} label={`${short}: closes and recorded fair value`} className="mx-auto" />;
    }
    case "next_payment": {
      const n = row?.next_payment;
      if (!n) return <Missing why={row?.pays_dividend === false ? "Pays no dividend" : "No payment expected"} />;
      const amt = toNum(n.amount);
      return (
        <>
          {formatDate(n.date)}
          <span className="block text-[11px] text-text-muted">
            {amt !== null ? formatMoney(amt, n.currency ?? x.baseCcy) : formatPrice(toNum(n.per_share), n.currency ?? h.instrument_currency)} · {n.kind}
          </span>
        </>
      );
    }
    case "income_12m": {
      const v = toNum(row?.income_12m);
      return v === null ? <Missing why={missingReason(id, row)} /> : formatMoney(v, row?.income_currency ?? x.baseCcy, { decimals: 0 });
    }
    case "dividend_quality":
      return row?.dividend_applicable ? (
        <span title={row.dividend_tier ?? undefined}>
          {row.dividend_passes} of {row.dividend_applicable}
        </span>
      ) : (
        <Missing why={row?.pays_dividend === false ? "Pays no dividend" : "Not scored yet"} />
      );
    case "mark":
      return row?.mark && Object.keys(row.mark).length ? (
        <ScoreMark petals={petalsFromMap(row.mark)} label={h.name ?? h.ticker} className="mx-auto" />
      ) : (
        <Missing why="Checks not computed yet" />
      );
    case "notes":
      return (
        <button
          type="button"
          onClick={() => x.onOpen("notes")}
          className="inline-flex items-center gap-1 text-xs text-text-muted hover:text-accent-ink"
          aria-label={`${row?.notes ?? 0} notes on ${h.name ?? h.ticker}`}
        >
          <NotebookPenIcon aria-hidden="true" className="size-3.5" />
          {row?.notes ? row.notes : "Add"}
        </button>
      );
    case "market_cap": {
      const v = insightNumber(id, row);
      return v === null ? <Missing why={missingReason(id, row)} /> : formatCompact(v);
    }
    default: {
      const v = insightNumber(id, row);
      if (v === null) return <Missing why={missingReason(id, row)} />;
      if (def.format === "percent") return formatPercent(v, { decimals: 1 });
      if (def.format === "multiple") return `${v.toFixed(1)}×`;
      if (id.startsWith("score_")) return `${v} / 6`;
      return String(v);
    }
  }
}

function HoldingHeader({ x }: { x: CellContext }) {
  const short = displayTicker(x.h.ticker);
  const cls = securityClass(x.h.security_type);
  return (
    <div className="flex items-center justify-between gap-2">
      <button type="button" onClick={() => x.onOpen()} className="group flex min-w-0 items-center gap-2.5 text-left">
        <span aria-hidden="true" className="grid size-[34px] shrink-0 place-items-center border border-border font-mono text-[10px] font-medium">
          {short.slice(0, 4)}
        </span>
        <span className="min-w-0">
          <span className="block max-w-[16rem] truncate font-semibold text-text group-hover:text-accent-ink group-hover:underline">
            {x.h.name ?? x.h.ticker}
          </span>
          <span className="block font-mono text-[11px] text-text-muted" title={x.h.ticker}>
            {short}
            {cls ? ` · ${cls}` : ""}
          </span>
        </span>
      </button>
      {x.expand ? <FundExpandButton open={x.expand.open} onToggle={x.expand.onToggle} controls={x.expand.controls} name={x.h.name ?? x.h.ticker} /> : null}
    </div>
  );
}

export function PresetPicker({
  value,
  onChange,
  onEdit,
  editing,
}: {
  value: PresetKey;
  onChange: (k: PresetKey) => void;
  onEdit: () => void;
  editing: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div role="radiogroup" aria-label="Columns preset" className="flex flex-wrap border border-border text-[13px]">
        {PRESETS.map((p, i) => (
          <button
            key={p.key}
            type="button"
            role="radio"
            aria-checked={value === p.key}
            title={p.description}
            onClick={() => onChange(p.key)}
            className={cn(
              "px-3 py-1.5",
              i > 0 && "border-l border-border",
              value === p.key ? "bg-accent text-accent-foreground" : "text-text hover:bg-border-soft",
            )}
          >
            {p.label}
          </button>
        ))}
      </div>
      {value === "custom" ? (
        <Button variant="outline" size="sm" onClick={onEdit} aria-expanded={editing} aria-controls="column-editor">
          <Columns3Icon aria-hidden="true" />
          {editing ? "Done" : "Choose columns"}
        </Button>
      ) : null}
    </div>
  );
}

const GROUPS: ColumnGroup[] = ["Position", "Check-in", "Value", "Income", "Score", "Fundamentals"];

export function ColumnEditor({ columns, onChange }: { columns: readonly ColumnId[]; onChange: (c: ColumnId[]) => void }) {
  const [dragging, setDragging] = useState<ColumnId | null>(null);
  return (
    <Blueprint id="column-editor" className="space-y-4 px-5 py-4">
      <div>
        <h3 className="text-sm font-semibold">Your columns, in order</h3>
        <p className="text-xs text-text-muted">Drag to reorder, or use the arrows. The holding stays first. Up to {MAX_CUSTOM} columns.</p>
        <ol className="mt-2 flex flex-wrap gap-1.5" aria-label="Chosen columns">
          {columns.map((id, i) => (
            <li
              key={id}
              draggable={id !== "holding"}
              onDragStart={() => setDragging(id)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => {
                if (dragging) onChange(dropColumn(columns, dragging, id));
                setDragging(null);
              }}
              className={cn(
                "flex items-center gap-1 border border-border bg-surface px-1.5 py-1 text-xs",
                dragging === id && "opacity-50",
                id !== "holding" && "cursor-grab",
              )}
            >
              {id !== "holding" ? <GripVerticalIcon aria-hidden="true" className="size-3 text-text-faint" /> : null}
              <span>{COLUMNS[id].label}</span>
              {id !== "holding" ? (
                <>
                  <button type="button" disabled={i <= 1} onClick={() => onChange(moveColumn(columns, id, -1))} aria-label={`Move ${COLUMNS[id].label} left`} className="p-0.5 text-text-muted hover:text-text disabled:opacity-30">
                    <ArrowLeftIcon aria-hidden="true" className="size-3" />
                  </button>
                  <button type="button" disabled={i === columns.length - 1} onClick={() => onChange(moveColumn(columns, id, 1))} aria-label={`Move ${COLUMNS[id].label} right`} className="p-0.5 text-text-muted hover:text-text disabled:opacity-30">
                    <ArrowRightIcon aria-hidden="true" className="size-3" />
                  </button>
                  <button type="button" onClick={() => onChange(toggleColumn(columns, id))} aria-label={`Remove ${COLUMNS[id].label}`} className="p-0.5 text-text-muted hover:text-loss">
                    <XIcon aria-hidden="true" className="size-3" />
                  </button>
                </>
              ) : null}
            </li>
          ))}
        </ol>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {GROUPS.map((g) => (
          <fieldset key={g}>
            <legend className="text-xs font-semibold text-text-muted">{g}</legend>
            <ul className="mt-1 space-y-0.5">
              {COLUMN_IDS.filter((id) => id !== "holding" && COLUMNS[id].group === g).map((id) => (
                <li key={id}>
                  <label className="flex items-start gap-2 text-sm" title={COLUMNS[id].help}>
                    <input
                      type="checkbox"
                      checked={columns.includes(id)}
                      disabled={!columns.includes(id) && columns.length >= MAX_CUSTOM}
                      onChange={() => onChange(toggleColumn(columns, id))}
                      className="mt-1 accent-accent"
                    />
                    <span>
                      {COLUMNS[id].label}
                      <span className="block text-[11px] text-text-muted">{COLUMNS[id].help}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
        ))}
      </div>
    </Blueprint>
  );
}

export function PresetTable({
  data,
  columns,
  insight,
  insightNote,
  priceMap,
  dayChange,
  onOpen,
}: {
  data: HoldingsResponse;
  columns: readonly ColumnId[];
  insight: ReadonlyMap<string, HoldingInsight>;
  insightNote: string | null;
  priceMap: Map<string, HoldingPrices>;
  dayChange: (t: string) => number | null;
  onOpen: (ticker: string, tab?: "overview" | "notes") => void;
}) {
  const [sort, setSort] = useState<SortState>({ id: "value", dir: "desc" });
  const active = useMemo<SortState>(
    () => (columns.includes(sort.id) ? sort : columns.includes("value") ? { id: "value", dir: "desc" } : { id: "holding", dir: "asc" }),
    [columns, sort],
  );
  const rows = useMemo(() => sortRows(data.holdings, active, insight, dayChange), [data.holdings, active, insight, dayChange]);
  const baseCcy = data.base_currency ?? "";
  const scale = weightScale(data.holdings);
  const totalValue = data.holdings.reduce((s, h) => s + h.value_base, 0);
  const invested = investedShare(data.holdings);
  const pnl = unrealisedTotal(data.holdings);
  const lookThrough = useLookThrough();
  const funds = useMemo(() => fundsByTicker(lookThrough.report), [lookThrough.report]);
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const toggle = (ticker: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(ticker)) next.add(ticker);
      return next;
    });
  const ctx = (h: Holding): CellContext => {
    const expandable = securityClass(h.security_type) === "ETF" || funds.has(h.ticker);
    return {
      h,
      row: insight.get(h.ticker),
      prices: priceMap.get(h.ticker),
      baseCcy,
      scale,
      onOpen: (tab) => onOpen(h.ticker, tab),
      expand: expandable ? { open: open.has(h.ticker), onToggle: () => toggle(h.ticker), controls: `look-through-${h.ticker}` } : undefined,
    };
  };
  const rest = columns.filter((c) => c !== "holding");
  const footer = (id: ColumnId): ReactNode => {
    if (id === "value") return formatMoney(totalValue, baseCcy, { decimals: 0 });
    if (id === "weight") return invested !== null ? `${formatPercent(invested)} invested` : null;
    if (id === "ppl")
      return pnl ? (
        <span className={TONE_CLASS[gainLossTone(pnl.ppl)]}>
          {formatMoney(pnl.ppl, pnl.currency, { decimals: 0, signed: true })}
          <span className="block text-xs font-normal">{formatPercent(pnl.pct, { signed: true })}</span>
        </span>
      ) : (
        "—"
      );
    return null;
  };
  const sortLabel = COLUMNS[active.id].label;

  return (
    <>
      {insightNote ? (
        <p className="text-sm text-text-muted" role="status">
          {insightNote}
        </p>
      ) : null}
      <Blueprint className="hidden overflow-visible md:block">
        <div className="relative overflow-x-auto">
          <table className="w-full text-sm" style={{ minWidth: `${Math.max(48, 16 + rest.length * 8)}rem` }}>
            <caption className="sr-only">
              Open positions, sorted by {sortLabel} {active.dir === "asc" ? "ascending" : "descending"}
            </caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
                {columns.map((id) => {
                  const def = COLUMNS[id];
                  const on = active.id === id;
                  const SortIcon = !on ? ArrowUpDownIcon : active.dir === "asc" ? ArrowUpIcon : ArrowDownIcon;
                  const label = id === "value" ? `${def.label} (${baseCcy})` : def.label;
                  return (
                    <th
                      key={id}
                      scope="col"
                      aria-sort={on ? (active.dir === "asc" ? "ascending" : "descending") : undefined}
                      title={def.help}
                      className={cn(
                        "px-3 py-2.5 font-medium first:pl-5 last:pr-5",
                        def.align === "right" && "text-right",
                        def.align === "center" && "text-center",
                        def.align === "left" && "text-left",
                      )}
                    >
                      {def.sortable ? (
                        <button
                          type="button"
                          onClick={() => setSort(nextSortState(active, id))}
                          className={cn("inline-flex items-center gap-1 uppercase hover:text-text", on && "text-text", def.align === "right" && "flex-row-reverse")}
                        >
                          <SortIcon aria-hidden="true" className="size-3" />
                          {label}
                        </button>
                      ) : (
                        label
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((h) => {
                const x = ctx(h);
                return (
                  <Fragment key={h.ticker}>
                    <tr className="border-b border-border-soft align-middle hover:bg-border-soft">
                      <th scope="row" className="py-2.5 pr-3 pl-5 text-left font-normal">
                        <HoldingHeader x={x} />
                      </th>
                      {rest.map((id) => (
                        <td
                          key={id}
                          className={cn(
                            "num px-3 py-2.5 last:pr-5",
                            COLUMNS[id].align === "right" && "text-right",
                            COLUMNS[id].align === "center" && "text-center",
                            id === "weight" && "w-44",
                            id === "spark" && "w-32",
                          )}
                        >
                          {cellContent(id, x)}
                        </td>
                      ))}
                    </tr>
                    {x.expand && x.expand.open ? (
                      <FundLookThroughRow
                        id={x.expand.controls}
                        fund={funds.get(h.ticker) ?? null}
                        loading={lookThrough.loading}
                        error={lookThrough.error}
                        colSpan={columns.length}
                        currency={baseCcy}
                      />
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="font-semibold">
                <th scope="row" className="py-3 pr-3 pl-5 text-left">
                  Total · {data.holdings.length} position{data.holdings.length === 1 ? "" : "s"}
                </th>
                {rest.map((id) => (
                  <td key={id} className={cn("num px-3 py-3 last:pr-5", COLUMNS[id].align === "right" && "text-right")}>
                    {footer(id)}
                  </td>
                ))}
              </tr>
            </tfoot>
          </table>
        </div>
      </Blueprint>
      <MobileCards rows={rows} rest={rest} ctx={ctx} />
    </>
  );
}

/** Below md: one card per holding with the preset's columns as label and value pairs. */
function MobileCards({ rows, rest, ctx }: { rows: Holding[]; rest: ColumnId[]; ctx: (h: Holding) => CellContext }) {
  return (
    <ul className="space-y-3 md:hidden" aria-label="Open positions">
      {rows.map((h) => {
        const x = ctx(h);
        return (
          <Blueprint as="li" key={h.ticker} className="p-4">
            <HoldingHeader x={{ ...x, expand: undefined }} />
            <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2">
              {rest.map((id) => (
                <div key={id} className={cn("min-w-0", (id === "thesis" || id === "last_event" || id === "weight") && "col-span-2")}>
                  <dt className="text-[11px] text-text-muted">{COLUMNS[id].label}</dt>
                  <dd className="num text-sm">{cellContent(id, x)}</dd>
                </div>
              ))}
            </dl>
          </Blueprint>
        );
      })}
    </ul>
  );
}
