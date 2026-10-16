"use client";

import {
  CandlestickChartIcon,
  LayoutGridIcon,
  LoaderCircleIcon,
  RefreshCwIcon,
  Table2Icon,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Heatmap, SparkArea } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { FreshnessLabel } from "@/components/portfolio/freshness-label";
import { HoldingsTabs } from "@/components/portfolio/holdings-tabs";
import { DriftAndComplianceSection } from "@/components/portfolio/moved-sections";
import { PortfolioTreemap, TreemapModeSwitch } from "@/components/portfolio/portfolio-treemap";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type Holding, type HoldingsResponse, type PortfolioHistory } from "@/lib/api/client";
import { formatDate, formatPercent, formatPrice, gainLossTone } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { PRESETS, presetColumns, type ColumnId, type HoldingInsight, type PresetKey } from "@/lib/portfolio/holding-columns";
import {
  compactMoney,
  dailyMoveGrid,
  dayExtremes,
  displayTicker,
  periodChange,
  portfolioDayMove,
  positionMixLabel,
  priceIndex,
  unrealisedTotal,
  type HoldingPrices,
} from "@/lib/portfolio/holdings-view";
import { TREEMAP_MODES, type TreemapMode } from "@/lib/portfolio/treemap";
import { dayMove } from "@/lib/prices/intraday";
import { cn } from "@/lib/utils";

import { HoldingDrawer, type DrawerTabKey } from "./holding-drawer";
import { Change, ColumnEditor, PresetPicker, PresetTable } from "./preset-table";

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
            value === key ? "bg-accent text-accent-foreground" : "text-text hover:bg-surface-muted",
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
            <HoldingsPresets data={data} priceMap={priceMap} dayChange={dayChange} />
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
                <Link href={`/companies/${encodeURIComponent(m.holding.ticker)}`} className="hover:text-accent-ink">
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
                <Link href={`/companies/${encodeURIComponent(h.ticker)}`} className="group min-w-0">
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

const fetchInsight = () => getBrowserApiClient().GET("/portfolio/holdings/insight");
const fetchViews = () => getBrowserApiClient().GET("/config/holdings-views");

/*
 * Holdings presets (insight plan I9): the table shows one of the fixed presets or the owner's
 * custom columns, read from one batched insight request; the choice is saved as the
 * `holdings_views` config. A row opens the holding drawer.
 */
function HoldingsPresets({
  data,
  priceMap,
  dayChange,
}: {
  data: HoldingsResponse;
  priceMap: Map<string, HoldingPrices>;
  dayChange: (t: string) => number | null;
}) {
  const insight = useApiResource(fetchInsight);
  const views = useApiResource(fetchViews).state;
  const [local, setLocal] = useState<{ selected: PresetKey; custom: ColumnId[] } | null>(null);
  const [editing, setEditing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<{ ticker: string; tab: DrawerTabKey } | null>(null);
  const [noteCounts, setNoteCounts] = useState<Record<string, number>>({});
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // `?drawer=TICKER&tab=notes` (the chat cites notes with this link) opens the drawer once.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const t = q.get("drawer");
    const tab = q.get("tab");
    if (t)
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reads the URL once on mount
      setDrawer({
        ticker: t,
        tab: tab === "position" || tab === "updates" || tab === "notes" ? tab : "overview",
      });
  }, []);

  const saved = views.kind === "ok" ? views.data.body : null;
  const selected: PresetKey = local?.selected ?? (saved?.selected as PresetKey | undefined) ?? "holding";
  const custom = local?.custom ?? presetColumns("custom", saved?.custom_columns);
  const columns = selected === "custom" ? custom : presetColumns(selected, null);

  const persist = useCallback((next: { selected: PresetKey; custom: ColumnId[] }) => {
    setLocal(next);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      const { error } = await getBrowserApiClient().PUT("/config/holdings-views", {
        body: { schema_version: 1, selected: next.selected, custom_columns: next.custom, basis: "headline" },
      });
      setSaveError(error ? "Your column choice could not be saved; it applies until you leave the page." : null);
    }, 700);
  }, []);
  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
  }, []);

  const rows = useMemo(() => (insight.state.kind === "ok" ? insight.state.data.holdings : []), [insight.state]);
  const byTicker = useMemo(() => {
    const m = new Map<string, HoldingInsight>();
    for (const r of rows) m.set(r.ticker, noteCounts[r.ticker] !== undefined ? { ...r, notes: noteCounts[r.ticker] } : r);
    return m;
  }, [rows, noteCounts]);
  const note =
    insight.state.kind === "loading"
      ? "Loading fair values, scores and fundamentals…"
      : insight.state.kind === "error"
        ? "Fair values, scores and fundamentals could not be loaded; those columns show a dash."
        : null;
  const onNotes = useCallback((ticker: string, n: number) => setNoteCounts((c) => (c[ticker] === n ? c : { ...c, [ticker]: n })), []);
  const description = PRESETS.find((p) => p.key === selected)?.description;

  return (
    <section aria-label="Holdings table" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <PresetPicker
          value={selected}
          onChange={(k) => {
            persist({ selected: k, custom });
            setEditing(k === "custom" && editing);
          }}
          onEdit={() => setEditing((e) => !e)}
          editing={editing}
        />
        <p className="text-xs text-text-muted">{description}</p>
      </div>
      {saveError ? (
        <p role="alert" className="text-sm text-loss">
          {saveError}
        </p>
      ) : null}
      {editing && selected === "custom" ? <ColumnEditor columns={custom} onChange={(c) => persist({ selected, custom: c })} /> : null}
      <PresetTable
        data={data}
        columns={columns}
        insight={byTicker}
        insightNote={note}
        priceMap={priceMap}
        dayChange={dayChange}
        onOpen={(ticker, tab) => setDrawer({ ticker, tab: tab ?? "overview" })}
      />
      <HoldingDrawer
        holding={drawer ? (data.holdings.find((h) => h.ticker === drawer.ticker) ?? null) : null}
        row={drawer ? byTicker.get(drawer.ticker) : undefined}
        baseCcy={data.base_currency ?? ""}
        tab={drawer?.tab ?? "overview"}
        onTab={(tab) => setDrawer((d) => (d ? { ...d, tab } : d))}
        onClose={() => setDrawer(null)}
        onNotes={onNotes}
      />
      <div className="pt-2">
        <DriftAndComplianceSection />
      </div>
    </section>
  );
}

