"use client";

import {
  ArrowDownIcon,
  ArrowUpDownIcon,
  ArrowUpIcon,
  CandlestickChartIcon,
  LayoutGridIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  Table2Icon,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { Fragment, useCallback, useMemo, useState, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { HBar, Heatmap, SparkArea, Sparkline } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { BandBadge } from "@/components/portfolio/band-badge";
import { FreshnessLabel } from "@/components/portfolio/freshness-label";
import { FundExpandButton, FundLookThroughRow } from "@/components/portfolio/fund-look-through-row";
import { HoldingsTabs } from "@/components/portfolio/holdings-tabs";
import { useLookThrough } from "@/components/portfolio/look-through-exposure";
import { PortfolioTreemap, TreemapModeSwitch } from "@/components/portfolio/portfolio-treemap";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type Holding, type HoldingsResponse, type PortfolioHistory } from "@/lib/api/client";
import {
  formatDate,
  formatMoney,
  formatPercent,
  formatPp,
  formatPrice,
  formatQuantity,
  gainLossTone,
} from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  DEFAULT_HOLDINGS_SORT,
  nextSort,
  sortHoldings,
  type HoldingsSort,
  type HoldingsSortKey,
} from "@/lib/portfolio/holdings-sort";
import {
  compactMoney,
  dailyMoveGrid,
  dayExtremes,
  displayTicker,
  investedShare,
  periodChange,
  portfolioDayMove,
  positionMixLabel,
  priceIndex,
  securityClass,
  unrealisedTotal,
  weightScale,
  type HoldingPrices,
} from "@/lib/portfolio/holdings-view";
import { fundsByTicker } from "@/lib/portfolio/look-through";
import { TREEMAP_MODES, type TreemapMode } from "@/lib/portfolio/treemap";
import { dayMove } from "@/lib/prices/intraday";
import { cn } from "@/lib/utils";

const fetchHoldings = () => getBrowserApiClient().GET("/portfolio/holdings");
const fetchPrices = () => getBrowserApiClient().GET("/portfolio/holdings/prices", { params: { query: { days: 30 } } });
const fetchHistory = () => getBrowserApiClient().GET("/portfolio/history", { params: { query: { days: 30 } } });

const TONE_CLASS = { gain: "text-gain", loss: "text-loss", flat: "" };

const VIEWS: { key: HoldingsViewKind; label: string; Icon: LucideIcon }[] = [
  { key: "table", label: "Table", Icon: Table2Icon },
  { key: "heatmap", label: "Heatmap", Icon: LayoutGridIcon },
  { key: "charts", label: "Charts", Icon: CandlestickChartIcon },
];
type HoldingsViewKind = "table" | "heatmap" | "charts";

/** "▲ +0.28%": a signed change with an arrow, so direction never rests on colour alone. */
function Change({ value, digits = 2, suffix }: { value: number | null | undefined; digits?: number; suffix?: ReactNode }) {
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

export function HoldingsView() {
  const { state, reload } = useApiResource(fetchHoldings);
  const prices = useApiResource(fetchPrices).state;
  const history = useApiResource(fetchHistory).state;
  const [view, setView] = useState<HoldingsViewKind>("table");
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const priceRows = prices.kind === "ok" ? prices.data.holdings : prices.kind === "error" ? (prices.previous?.holdings ?? null) : null;
  const historyData = history.kind === "ok" ? history.data : null;

  return (
    <div className="space-y-[22px]">
      <PageHeader
        kicker="Portfolio"
        title="Holdings"
        description="Every open Trading212 position. Open one for its thesis, verdicts and evidence."
        aside={
          data?.freshness ? (
            <FreshnessLabel syncedAt={data.freshness.synced_at} snapshotDate={data.freshness.snapshot_date} />
          ) : null
        }
      />
      <HoldingsTabs aside={data?.freshness && data.holdings.length > 0 ? <ViewSwitch value={view} onChange={setView} /> : null} />

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading holdings…
        </p>
      ) : null}

      {state.kind === "error" ? (
        <AlertMessage title={state.title}>
          <p>{state.detail}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => void reload()}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}

      {data ? (
        <HoldingsContent
          data={data}
          view={view}
          prices={priceRows}
          pricesFailed={prices.kind === "error"}
          history={historyData}
        />
      ) : null}
    </div>
  );
}

function ViewSwitch({ value, onChange }: { value: HoldingsViewKind; onChange: (v: HoldingsViewKind) => void }) {
  return (
    <div role="group" aria-label="Holdings view" className="inline-flex border border-border text-[13px]">
      {VIEWS.map(({ key, label, Icon }, i) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          onClick={() => onChange(key)}
          className={cn(
            "inline-flex items-center gap-1.5 px-3 py-1.5",
            i > 0 && "border-l border-border",
            value === key ? "bg-accent text-accent-foreground" : "text-text hover:bg-border-soft",
          )}
        >
          <Icon aria-hidden="true" className="size-3.5" />
          {label}
        </button>
      ))}
    </div>
  );
}

function HoldingsContent({
  data,
  view,
  prices,
  pricesFailed,
  history,
}: {
  data: HoldingsResponse;
  view: HoldingsViewKind;
  prices: HoldingPrices[] | null;
  pricesFailed: boolean;
  history: PortfolioHistory | null;
}) {
  const priceMap = useMemo(() => priceIndex(prices), [prices]);
  // The intraday change (Yahoo, unofficial) when its session is newer than the last close.
  const dayChange = useCallback((ticker: string) => dayMove(priceMap.get(ticker), formatDate).value, [priceMap]);
  const [mode, setMode] = useState<TreemapMode>("day");

  if (!data.freshness) {
    return (
      <EmptyState title="No holdings yet">
        The first Trading212 sync lists your positions here. Use &ldquo;Sync now&rdquo; in the header to run one.
      </EmptyState>
    );
  }
  if (data.holdings.length === 0) {
    return (
      <EmptyState title="No open positions">Trading212 reported no open positions in the latest sync.</EmptyState>
    );
  }

  const baseCcy = data.base_currency ?? "";
  return (
    <>
      <KpiTiles holdings={data.holdings} baseCcy={baseCcy} priceMap={priceMap} dayChange={dayChange} history={history} />
      {pricesFailed ? (
        <p className="text-sm text-text-muted" role="status">
          Daily closes could not be loaded, so today&rsquo;s moves and the 30-day lines are missing.
        </p>
      ) : null}
      {view === "charts" ? (
        <ChartsGrid holdings={data.holdings} priceMap={priceMap} prices={prices} />
      ) : (
        <>
          <TreemapPanel
            holdings={data.holdings}
            mode={mode}
            onMode={setMode}
            dayChange={dayChange}
            aspectRatio={view === "heatmap" ? 2.6 : 4.2}
          />
          {view === "heatmap" ? (
            <DailyMovesPanel prices={prices} />
          ) : (
            <HoldingsTable data={data} priceMap={priceMap} dayChange={dayChange} />
          )}
        </>
      )}
    </>
  );
}

function Tile({
  label,
  value,
  sub,
  spark,
  sparkLabel,
}: {
  label: string;
  value: ReactNode;
  sub: ReactNode;
  spark?: readonly (number | null)[] | null;
  sparkLabel?: string;
}) {
  return (
    <div className="flex items-end justify-between gap-2.5 border-border px-5 py-4 [&:not(:first-child)]:border-t sm:[&:not(:first-child)]:border-t-0 sm:[&:not(:first-child)]:border-l">
      <div className="min-w-0">
        <div className="text-xs text-text-muted">{label}</div>
        <div className="num font-heading text-[28px] leading-[1.1] font-semibold">{value}</div>
        <div className="text-xs text-text-muted">{sub}</div>
      </div>
      {spark && spark.filter((v) => v !== null).length > 1 ? (
        <SparkArea values={spark} width={90} height={36} className="h-9 w-[90px] shrink-0" label={sparkLabel} />
      ) : null}
    </div>
  );
}

function KpiTiles({
  holdings,
  baseCcy,
  priceMap,
  dayChange,
  history,
}: {
  holdings: Holding[];
  baseCcy: string;
  priceMap: Map<string, HoldingPrices>;
  dayChange: (t: string) => number | null;
  history: PortfolioHistory | null;
}) {
  const total = holdings.reduce((s, h) => s + h.value_base, 0);
  const move = portfolioDayMove(holdings, dayChange);
  const pnl = unrealisedTotal(holdings);
  const extremes = dayExtremes(holdings, dayChange);
  const values = history && history.values_currency === baseCcy ? history.values : [];
  const closes = (t: string) => priceMap.get(t)?.bars.map((b) => b.close) ?? null;

  return (
    <Blueprint aria-label="Holdings at a glance" className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5">
      <Tile label="Positions" value={holdings.length} sub={positionMixLabel(holdings)} />
      <Tile
        label="Market value"
        value={compactMoney(total, baseCcy)}
        sub={
          move ? (
            <Change
              value={move.change}
              suffix={<span className="text-text-muted"> last close{move.covered < holdings.length ? ` · ${move.covered} of ${holdings.length}` : ""}</span>}
            />
          ) : (
            "No daily closes yet"
          )
        }
        spark={values.map((v) => v.invested)}
        sparkLabel={`Market value of positions, last ${values.length} snapshots`}
      />
      <Tile
        label="Unrealised P/L"
        value={
          pnl ? (
            <span className={TONE_CLASS[gainLossTone(pnl.ppl)]}>{compactMoney(pnl.ppl, pnl.currency, true)}</span>
          ) : (
            "—"
          )
        }
        sub={
          pnl ? (
            <span className={TONE_CLASS[gainLossTone(pnl.ppl)]}>{formatPercent(pnl.pct, { signed: true })} on cost</span>
          ) : (
            "Mixed account currencies"
          )
        }
        spark={values.map((v) => v.unrealised)}
        sparkLabel={`Unrealised P/L, last ${values.length} snapshots`}
      />
      {(["best", "worst"] as const).map((k) => {
        const m = extremes?.[k];
        return (
          <Tile
            key={k}
            label={k === "best" ? "Best today" : "Worst today"}
            value={
              m ? (
                <Link href={`/holdings/${encodeURIComponent(m.holding.ticker)}`} className="hover:text-accent-ink">
                  {displayTicker(m.holding.ticker)}{" "}
                  <span className={TONE_CLASS[gainLossTone(m.change)]}>{formatPercent(m.change, { signed: true })}</span>
                </Link>
              ) : (
                "—"
              )
            }
            sub={m ? (m.holding.name ?? m.holding.ticker) : "No daily closes yet"}
            spark={m ? closes(m.holding.ticker) : null}
            sparkLabel={m ? `${displayTicker(m.holding.ticker)}, 30 days` : undefined}
          />
        );
      })}
    </Blueprint>
  );
}

function TreemapPanel({
  holdings,
  mode,
  onMode,
  dayChange,
  aspectRatio,
}: {
  holdings: Holding[];
  mode: TreemapMode;
  onMode: (m: TreemapMode) => void;
  dayChange: (t: string) => number | null;
  aspectRatio: number;
}) {
  const m = TREEMAP_MODES[mode];
  return (
    <Blueprint className="flex flex-col gap-3 px-[22px] py-5" aria-labelledby="treemap-heading">
      <PanelHeading
        kicker={m.kicker}
        title={<span id="treemap-heading">{m.title}</span>}
        action={<TreemapModeSwitch value={mode} onChange={onMode} />}
      />
      <div className="relative overflow-x-auto">
        <PortfolioTreemap holdings={holdings} mode={mode} dayChange={dayChange} aspectRatio={aspectRatio} />
      </div>
      <p className="text-xs text-text-muted">
        Tile area is the position&rsquo;s weight; colour is the {m.description}, saturating at ±
        {formatPercent(m.cap, { decimals: 0 })}. Grey tiles have no figure.
      </p>
    </Blueprint>
  );
}

function DailyMovesPanel({ prices }: { prices: HoldingPrices[] | null }) {
  const grid = useMemo(() => dailyMoveGrid(prices ?? [], 20), [prices]);
  return (
    <Blueprint className="flex flex-col gap-3 px-[22px] py-5" aria-labelledby="moves-heading">
      <PanelHeading kicker="Last 20 sessions" title={<span id="moves-heading">Daily moves by holding</span>} />
      {grid.rows.length === 0 ? (
        <p className="text-sm text-text-muted">No daily closes are stored for the holdings yet.</p>
      ) : (
        <div className="relative overflow-x-auto">
          <Heatmap
            className="min-w-[48rem]"
            rows={grid.rows}
            columns={grid.dates.map((d) => d.slice(8))}
            columnNames={grid.dates.map((d) => formatDate(d))}
            cap={0.03}
            caption="Close-to-close move per holding and trading day, in percent"
          />
        </div>
      )}
      <p className="text-xs text-text-muted">
        Each cell is the holding&rsquo;s close over its previous stored close (Stooq, or Yahoo Finance where Stooq has
        none), in percent; column headers are the day of the month. Dashed cells have no close that day.
      </p>
    </Blueprint>
  );
}

function ChartsGrid({
  holdings,
  priceMap,
  prices,
}: {
  holdings: Holding[];
  priceMap: Map<string, HoldingPrices>;
  prices: HoldingPrices[] | null;
}) {
  return (
    <section aria-labelledby="charts-heading" className="space-y-3">
      <PanelHeading kicker="Last 30 days" title={<span id="charts-heading">Price path by holding</span>} />
      <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {holdings.map((h) => {
          const p = priceMap.get(h.ticker);
          const bars = p?.bars ?? [];
          const ch30 = periodChange(bars);
          return (
            <Blueprint as="li" key={h.ticker} className="flex flex-col gap-2 p-4">
              <div className="flex items-start justify-between gap-2">
                <Link href={`/holdings/${encodeURIComponent(h.ticker)}`} className="group min-w-0">
                  <span className="block font-mono text-xs font-medium">{displayTicker(h.ticker)}</span>
                  <span className="block truncate text-sm font-semibold group-hover:text-accent-ink group-hover:underline">
                    {h.name ?? h.ticker}
                  </span>
                </Link>
                <span className="num shrink-0 text-right text-xs text-text-muted">
                  weight
                  <span className="block text-sm text-text">{formatPercent(h.weight)}</span>
                </span>
              </div>
              {bars.length > 1 ? (
                <SparkArea
                  values={bars.map((b) => b.close)}
                  tone="direction"
                  width={240}
                  height={64}
                  className="h-16"
                  label={`${displayTicker(h.ticker)} closes, ${formatDate(bars[0].date)} to ${formatDate(bars[bars.length - 1].date)}`}
                />
              ) : (
                <p className="flex h-16 items-center text-xs text-text-muted">
                  {prices === null ? "Loading closes…" : "No stored closes"}
                </p>
              )}
              <div className="num flex items-baseline justify-between gap-2 text-xs">
                <span>
                  {p?.last_close != null ? formatPrice(p.last_close, h.instrument_currency) : "—"}{" "}
                  <Change value={p?.day_change_pct} />
                </span>
                <span className="text-text-muted">
                  30 d <Change value={ch30} digits={1} />
                </span>
              </div>
            </Blueprint>
          );
        })}
      </ul>
      <p className="text-xs text-text-muted">
        Daily closes (Stooq, or Yahoo Finance where Stooq has none) in each instrument&rsquo;s currency; the line is
        gain-coloured when the last close is at or above the first.
      </p>
    </section>
  );
}

const COLUMNS: { key: HoldingsSortKey | null; label: string; align: "left" | "right" | "center"; className?: string }[] = [
  { key: "name", label: "Holding", align: "left" },
  { key: null, label: "Qty", align: "right" },
  { key: null, label: "Avg price", align: "right" },
  { key: "day", label: "Price", align: "right" },
  { key: null, label: "30 days", align: "center" },
  { key: "value", label: "Value", align: "right" },
  { key: "weight", label: "Weight vs target", align: "left" },
  { key: "drift", label: "Drift", align: "right" },
  { key: "ppl", label: "Unrealised P/L", align: "right" },
];

function HoldingsTable({
  data,
  priceMap,
  dayChange,
}: {
  data: HoldingsResponse;
  priceMap: Map<string, HoldingPrices>;
  dayChange: (t: string) => number | null;
}) {
  const [sort, setSort] = useState<HoldingsSort>(DEFAULT_HOLDINGS_SORT);
  const rows = useMemo(() => sortHoldings(data.holdings, sort, { dayChange }), [data.holdings, sort, dayChange]);
  const baseCcy = data.base_currency ?? "";
  const scale = weightScale(data.holdings);
  const totalValue = data.holdings.reduce((s, h) => s + h.value_base, 0);
  const invested = investedShare(data.holdings);
  const pnl = unrealisedTotal(data.holdings);
  const sortLabel = COLUMNS.find((c) => c.key === sort.key)?.label ?? sort.key;
  const lookThrough = useLookThrough();
  const funds = useMemo(() => fundsByTicker(lookThrough.report), [lookThrough.report]);
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set());
  const toggle = (ticker: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(ticker)) next.add(ticker);
      return next;
    });

  return (
    <Blueprint className="overflow-visible">
      <div className="relative overflow-x-auto">
        <table className="w-full min-w-[72rem] text-sm">
          <caption className="sr-only">
            Open positions, sorted by {sortLabel} {sort.direction === "asc" ? "ascending" : "descending"}
          </caption>
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
              {COLUMNS.map((column) => {
                const active = column.key !== null && sort.key === column.key;
                const ariaSort = active ? (sort.direction === "asc" ? "ascending" : "descending") : undefined;
                const SortIcon = !active ? ArrowUpDownIcon : sort.direction === "asc" ? ArrowUpIcon : ArrowDownIcon;
                const label = column.key === "value" ? `${column.label} (${baseCcy})` : column.label;
                return (
                  <th
                    key={column.label}
                    scope="col"
                    aria-sort={ariaSort}
                    className={cn(
                      "px-3 py-2.5 font-medium first:pl-5 last:pr-5",
                      column.align === "right" && "text-right",
                      column.align === "center" && "text-center",
                      column.align === "left" && "text-left",
                    )}
                  >
                    {column.key ? (
                      <button
                        type="button"
                        onClick={() => setSort((s) => nextSort(s, column.key!))}
                        className={cn(
                          "inline-flex items-center gap-1 uppercase hover:text-text",
                          active && "text-text",
                          column.align === "right" && "flex-row-reverse",
                        )}
                      >
                        <SortIcon aria-hidden="true" className="size-3" />
                        {label}
                        {column.key === "day" ? <span className="sr-only"> (sorts by today&rsquo;s move)</span> : null}
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
              const expandable = securityClass(h.security_type) === "ETF" || funds.has(h.ticker);
              const rowId = `look-through-${h.ticker}`;
              return (
                <Fragment key={h.ticker}>
                  <HoldingRow
                    h={h}
                    baseCcy={baseCcy}
                    scale={scale}
                    prices={priceMap.get(h.ticker)}
                    expand={expandable ? { open: open.has(h.ticker), onToggle: () => toggle(h.ticker), controls: rowId } : undefined}
                  />
                  {expandable && open.has(h.ticker) ? (
                    <FundLookThroughRow
                      id={rowId}
                      fund={funds.get(h.ticker) ?? null}
                      loading={lookThrough.loading}
                      error={lookThrough.error}
                      colSpan={COLUMNS.length}
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
              <td colSpan={4} />
              <td className="num px-3 py-3 text-right">{formatMoney(totalValue, baseCcy, { decimals: 0 })}</td>
              <td className="num px-3 py-3">{invested !== null ? `${formatPercent(invested)} invested` : null}</td>
              <td />
              <td className={cn("num py-3 pr-5 pl-3 text-right", pnl ? TONE_CLASS[gainLossTone(pnl.ppl)] : "")}>
                {pnl ? formatMoney(pnl.ppl, pnl.currency, { decimals: 0, signed: true }) : "—"}
                {pnl ? <span className="block text-xs font-normal">{formatPercent(pnl.pct, { signed: true })}</span> : null}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </Blueprint>
  );
}

function HoldingRow({
  h,
  baseCcy,
  scale,
  prices,
  expand,
}: {
  h: Holding;
  baseCcy: string;
  scale: number;
  prices: HoldingPrices | undefined;
  /** ETF rows: the disclosure for its underlying holdings (look-through). */
  expand?: { open: boolean; onToggle: () => void; controls: string };
}) {
  const tone = gainLossTone(h.ppl);
  const short = displayTicker(h.ticker);
  const cls = securityClass(h.security_type);
  const move = dayMove(prices, (d) => formatDate(d));
  const day = move.value;
  const bars = prices?.bars ?? [];
  return (
    <tr className="border-b border-border-soft align-middle hover:bg-border-soft">
      <th scope="row" className="py-2.5 pr-3 pl-5 text-left font-normal">
        <div className="flex items-center justify-between gap-2">
        <Link href={`/holdings/${encodeURIComponent(h.ticker)}`} className="group flex min-w-0 items-center gap-2.5">
          <span
            aria-hidden="true"
            className="grid size-[34px] shrink-0 place-items-center border border-border font-mono text-[10px] font-medium"
          >
            {short.slice(0, 4)}
          </span>
          <span className="min-w-0">
            <span className="block max-w-[16rem] truncate font-semibold text-text group-hover:text-accent-ink group-hover:underline">
              {h.name ?? h.ticker}
            </span>
            <span className="block font-mono text-[11px] text-text-muted" title={h.ticker}>
              {short}
              {cls ? ` · ${cls}` : ""}
            </span>
          </span>
        </Link>
        {expand ? <FundExpandButton open={expand.open} onToggle={expand.onToggle} controls={expand.controls} name={h.name ?? h.ticker} /> : null}
        </div>
      </th>
      <td className="num px-3 py-2.5 text-right">{formatQuantity(h.quantity)}</td>
      <td className="num px-3 py-2.5 text-right text-text-muted">{formatPrice(h.avg_price, h.instrument_currency)}</td>
      <td className="num px-3 py-2.5 text-right">
        {formatPrice(h.current_price, h.instrument_currency)}
        <span className="block text-[11px]" title={move.title}>
          {day === null ? <span className="text-text-muted">—</span> : <Change value={day} />}
          {move.kind === "intraday" && move.stamp ? (
            <span className="ml-1 font-mono text-[10px] text-text-faint">
              <span className="sr-only">intraday, </span>
              {move.stamp}
            </span>
          ) : null}
        </span>
      </td>
      <td className="w-32 px-3 py-2.5">
        {bars.length > 1 ? (
          <Sparkline
            values={bars.map((b) => b.close)}
            area
            width={100}
            height={30}
            className="h-[30px] w-full"
            label={`${short}, ${bars.length} closes`}
          />
        ) : (
          <span className="block text-center text-xs text-text-muted">—</span>
        )}
      </td>
      <td className="num px-3 py-2.5 text-right font-semibold">
        {formatMoney(h.value_base, baseCcy, { decimals: 0 })}
        {h.account_currency !== baseCcy || h.instrument_currency !== baseCcy ? (
          <span className="block text-xs font-normal text-text-muted" title="Trading212's own conversion">
            T212 {formatMoney(h.value_account, h.account_currency)}
          </span>
        ) : null}
      </td>
      <td className="w-44 px-3 py-2.5">
        <div className="num mb-1 flex justify-between gap-2 text-xs">
          <span>{formatPercent(h.weight)}</span>
          <span className="text-text-muted">
            {h.target_weight !== null ? `target ${formatPercent(h.target_weight)}` : "no target"}
          </span>
        </div>
        <HBar
          value={h.weight}
          max={scale}
          target={h.target_weight}
          size="sm"
          label={`${short} weight`}
          className="h-1.5"
        />
      </td>
      <td className="px-3 py-2.5 text-right">
        <span className="inline-flex flex-col items-end gap-1">
          {h.drift_status !== "no_target" ? <span className="num text-[13px]">{formatPp(h.drift)}</span> : null}
          <BandBadge status={h.drift_status} />
        </span>
      </td>
      <td className={cn("num py-2.5 pr-5 pl-3 text-right", TONE_CLASS[tone])}>
        <span className="font-semibold">{formatMoney(h.ppl, h.account_currency, { decimals: 0, signed: true })}</span>
        <span className="block text-xs">{formatPercent(h.ppl_pct, { signed: true })}</span>
      </td>
    </tr>
  );
}
