"use client";

import { ArrowLeftIcon, ArrowRightIcon, DownloadIcon, LoaderCircleIcon, RefreshCwIcon, SearchIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { HoldingsTabs } from "@/components/portfolio/holdings-tabs";
import { Blueprint, BlueprintCorners, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getBrowserApiClient,
  type Holding,
  type TotalByCurrency,
  type TransactionOut,
  type TransactionsPage,
} from "@/lib/api/client";
import { formatDate, formatMoney, formatQuantity, formatTime } from "@/lib/format";
import {
  activeTab,
  collectPages,
  csvFileName,
  EMPTY_TRANSACTION_FILTERS,
  isoDate,
  isTicker,
  parseTransactionFilters,
  periodPresets,
  tabCounts,
  TRANSACTION_TABS,
  TRANSACTION_TYPES,
  transactionFiltersToSearch,
  transactionQuery,
  transactionsCsv,
  TYPE_LABELS,
  TYPE_SINGULAR,
  withTab,
  type TransactionFilters,
} from "@/lib/portfolio/transactions";
import { shortTicker } from "@/lib/portfolio/ticker";
import { cn } from "@/lib/utils";

import { FlowCharts } from "./flow-charts";

const PAGE = 25;
const EXPORT_PAGE = 500;
const EXPORT_MAX_PAGES = 400;

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; page: TransactionsPage; paging: boolean; pageError: string | null; key: string };

async function fetchPage(filters: TransactionFilters, cursor: string | null, limit = PAGE): Promise<TransactionsPage> {
  const { data, error, response } = await getBrowserApiClient().GET("/portfolio/transactions", {
    params: { query: transactionQuery(filters, cursor, limit) },
  });
  if (!data) {
    const detail = error && typeof error === "object" && "detail" in error && typeof error.detail === "string" ? ` ${error.detail}` : "";
    throw new Error(`The API answered HTTP ${response.status}.${detail}`);
  }
  return data;
}

function message(err: unknown) {
  return err instanceof Error ? err.message : String(err);
}

/** An amount in its currency; a plain number when the row has no currency. */
function money(amount: number | null, currency: string | null, signed = false) {
  if (currency) return formatMoney(amount, currency, { signed });
  if (amount === null) return "—";
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(amount);
}

/** A unit price as Trading212 reports it (in the instrument's currency, which the row does not carry). */
function price(value: number | null) {
  if (value === null) return "—";
  return new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(value);
}

/** "MARKET_BUY" / "market_buy" -> "Market buy". */
function humanize(value: string | null): string {
  if (!value) return "—";
  const words = value.replaceAll("_", " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function periodLabel(filters: TransactionFilters): string {
  if (filters.from && filters.to) return `${formatDate(filters.from)} – ${formatDate(filters.to)}`;
  if (filters.from) return `since ${formatDate(filters.from)}`;
  if (filters.to) return `until ${formatDate(filters.to)}`;
  return "all time";
}

export function TransactionsView() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const search = params.toString();
  const filters = useMemo(() => parseTransactionFilters(new URLSearchParams(search)), [search]);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  // Cursor of each page shown so far: [null (newest), cursor of page 2, …]; the last is the current page.
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [counted, setCounted] = useState<{ key: string; counts: Record<string, number> } | null>(null);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  // Only the newest request's answer is applied (filters can change while a page loads).
  const sequence = useRef(0);

  const setFilters = useCallback(
    (next: TransactionFilters) => router.replace(`${pathname}${transactionFiltersToSearch(next)}`, { scroll: false }),
    [router, pathname],
  );

  const loadKey = `${search}|${reloadKey}`;
  // Fetches a page; state is only set once the answer arrives (see `turnPage` for the busy mark).
  const loadPage = useCallback(
    (stack: (string | null)[]) => {
      const mine = ++sequence.current;
      fetchPage(filters, stack[stack.length - 1])
        .then((page) => {
          if (mine !== sequence.current) return;
          setCursors(stack);
          setLoad({ kind: "ok", page, paging: false, pageError: null, key: loadKey });
        })
        .catch((err: unknown) => {
          if (mine !== sequence.current) return;
          setLoad((prev) => (prev.kind === "ok" ? { ...prev, paging: false, pageError: message(err) } : { kind: "error", message: message(err) }));
        });
    },
    [filters, loadKey],
  );

  useEffect(() => {
    loadPage([null]);
  }, [loadPage]);

  function turnPage(stack: (string | null)[]) {
    setLoad((prev) => (prev.kind === "ok" ? { ...prev, paging: true, pageError: null } : prev));
    loadPage(stack);
  }

  // Per-tab counts: the same ticker and period over every type.
  const countKey = `${filters.ticker}|${filters.from}|${filters.to}|${reloadKey}`;
  useEffect(() => {
    let live = true;
    fetchPage({ ...filters, types: [], subtypes: [] }, null, 1)
      .then((page) => live && setCounted({ key: countKey, counts: tabCounts(page.totals) }))
      .catch(() => undefined);
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- counts depend only on ticker and period
  }, [countKey]);

  useEffect(() => {
    getBrowserApiClient()
      .GET("/portfolio/holdings")
      .then((r) => r.data && setHoldings(r.data.holdings))
      .catch(() => undefined);
  }, []);

  const counts = counted?.key === countKey ? counted.counts : null;
  const busy = load.kind === "ok" && (load.paging || load.key !== loadKey);
  const filtered =
    filters.types.length > 0 || filters.subtypes.length > 0 || filters.ticker !== null || filters.from !== null || filters.to !== null;
  const pageIndex = cursors.length - 1;

  return (
    <div className="space-y-5">
      <PageHeader
        kicker="Portfolio"
        title="Transactions"
        description={
          <>
            Orders, dividends, interest and cash movements from Trading212, newest first. Totals cover every matching row, not just
            the page shown. Dates are UTC days, both ends included.
          </>
        }
        aside={
          <>
            <ExportCsv filters={filters} />
            <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
              <RefreshCwIcon aria-hidden="true" />
              Reload
            </Button>
          </>
        }
      />
      <HoldingsTabs />

      <FlowCharts key={reloadKey} />

      <section aria-labelledby="rows-heading" className="blueprint min-w-0">
        <BlueprintCorners />
        <h2 id="rows-heading" className="sr-only">
          Transactions
        </h2>
        <Toolbar key={search} filters={filters} holdings={holdings} counts={counts} onChange={setFilters} filtered={filtered} />

        {load.kind === "loading" ? (
          <p className="flex items-center gap-2 px-5 py-6 text-sm text-text-muted" role="status">
            <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
            Loading transactions…
          </p>
        ) : null}
        {load.kind === "error" ? (
          <div className="p-5">
            <AlertMessage title="Could not load the transactions">
              <p>{load.message}</p>
              <Button variant="outline" size="sm" className="mt-2" onClick={() => setReloadKey((k) => k + 1)}>
                <RefreshCwIcon aria-hidden="true" />
                Try again
              </Button>
            </AlertMessage>
          </div>
        ) : null}
        {load.kind === "ok" ? (
          <>
            {load.page.items.length === 0 ? (
              <div className="p-5">
                <EmptyState title={filtered ? "No transactions match these filters" : "No transactions yet"}>
                  {filtered ? "Widen the period or clear a filter." : "The portfolio sync imports orders, dividends and cash movements from Trading212."}
                </EmptyState>
              </div>
            ) : (
              <TransactionTable rows={load.page.items} busy={busy} />
            )}
            <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 text-[13px] text-text-muted">
              <p className="num" role="status">
                {load.page.items.length
                  ? `Showing ${pageIndex * PAGE + 1}–${pageIndex * PAGE + load.page.items.length} of ${load.page.total_count}`
                  : `Showing 0 of ${load.page.total_count}`}
                {busy ? " · loading…" : null}
              </p>
              {load.pageError ? <span className="text-text">Could not load that page: {load.pageError}</span> : null}
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={pageIndex === 0 || busy}
                  onClick={() => turnPage(cursors.slice(0, -1))}
                  className="font-sans text-[13px] font-normal"
                >
                  <ArrowLeftIcon aria-hidden="true" />
                  Newer
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!load.page.next_cursor || busy}
                  onClick={() => turnPage([...cursors, load.page.next_cursor])}
                  className="font-sans text-[13px] font-normal text-accent-ink"
                >
                  Older
                  <ArrowRightIcon aria-hidden="true" />
                </Button>
              </div>
            </div>
          </>
        ) : null}
      </section>

      {load.kind === "ok" ? (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
          <Totals totals={load.page.totals} count={load.page.total_count} />
          <Dividends filters={filters} dividends={load.page.dividends} />
        </div>
      ) : null}
    </div>
  );
}

/** Exports every row matching the filters (all pages, not just the one shown) as CSV. */
function ExportCsv({ filters }: { filters: TransactionFilters }) {
  const [state, setState] = useState<{ kind: "idle" } | { kind: "busy"; rows: number } | { kind: "error"; message: string }>({ kind: "idle" });

  async function run() {
    setState({ kind: "busy", rows: 0 });
    try {
      let seen = 0;
      const { items, complete } = await collectPages<TransactionOut>(async (cursor) => {
        const page = await fetchPage(filters, cursor, EXPORT_PAGE);
        seen += page.items.length;
        setState({ kind: "busy", rows: seen });
        return page;
      }, EXPORT_MAX_PAGES);
      if (!complete) throw new Error(`More than ${EXPORT_PAGE * EXPORT_MAX_PAGES} rows; narrow the period and export in parts.`);
      const blob = new Blob([transactionsCsv(items)], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = csvFileName(filters, new Date().toISOString().slice(0, 10));
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setState({ kind: "idle" });
    } catch (err) {
      setState({ kind: "error", message: message(err) });
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      {state.kind === "error" ? (
        <span className="text-xs text-text" role="alert">
          Export failed: {state.message}
        </span>
      ) : null}
      <Button variant="outline" onClick={() => void run()} disabled={state.kind === "busy"} title="Every row matching the filters, as CSV">
        {state.kind === "busy" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <DownloadIcon aria-hidden="true" />}
        {state.kind === "busy" ? `Exporting… ${state.rows}` : "Export CSV"}
      </Button>
    </span>
  );
}

function Toolbar({
  filters,
  holdings,
  counts,
  onChange,
  filtered,
}: {
  filters: TransactionFilters;
  holdings: Holding[];
  counts: Record<string, number> | null;
  onChange: (next: TransactionFilters) => void;
  filtered: boolean;
}) {
  const ids = { ticker: useId(), from: useId(), to: useId(), list: useId() };
  const [ticker, setTicker] = useState(filters.ticker ?? "");
  const [from, setFrom] = useState(filters.from ?? "");
  const [to, setTo] = useState(filters.to ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const presets = useMemo(() => periodPresets(), []);
  const tab = activeTab(filters);

  function apply(event: React.FormEvent) {
    event.preventDefault();
    const t = ticker.trim();
    if (t && !isTicker(t)) return setProblem("A ticker is letters, digits, dots, dashes or underscores (up to 32).");
    if ((from && !isoDate(from)) || (to && !isoDate(to))) return setProblem("Dates must be real calendar days.");
    if (from && to && from > to) return setProblem("The start date is after the end date.");
    setProblem(null);
    onChange({ ...filters, ticker: t || null, from: from || null, to: to || null });
  }

  return (
    <div className="space-y-3 border-b border-border px-5 py-3.5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div role="group" aria-label="Transaction type" className="inline-flex max-w-full overflow-x-auto border border-border text-[13px]">
          {TRANSACTION_TABS.map((t, i) => {
            const on = tab === t.id;
            const count = counts?.[t.id];
            return (
              <button
                key={t.id}
                type="button"
                aria-pressed={on}
                onClick={() => onChange(withTab(filters, t.id))}
                className={cn(
                  "px-3 py-1.5 whitespace-nowrap outline-offset-2 focus-visible:outline-2 focus-visible:outline-focus",
                  i > 0 && "border-l border-border",
                  on ? "bg-accent text-accent-foreground" : "text-text hover:bg-border-soft",
                )}
              >
                {t.label}
                {count !== undefined ? <span className={cn("num ml-1", on ? "" : "text-text-muted")}>· {count}</span> : null}
              </button>
            );
          })}
        </div>
        <form onSubmit={apply} className="flex flex-wrap items-end gap-2">
          <div className="w-44 space-y-1">
            <Label htmlFor={ids.ticker} className="text-xs text-text-muted">
              Ticker
            </Label>
            <div className="relative">
              <SearchIcon aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-text-muted" />
              <Input
                id={ids.ticker}
                value={ticker}
                list={ids.list}
                spellCheck={false}
                placeholder="Filter by ticker"
                className="h-8 pl-8"
                onChange={(e) => setTicker(e.target.value)}
              />
            </div>
            <datalist id={ids.list}>
              {holdings.map((h) => (
                <option key={h.ticker} value={h.ticker}>
                  {h.name ?? h.ticker}
                </option>
              ))}
            </datalist>
          </div>
          <div className="w-36 space-y-1">
            <Label htmlFor={ids.from} className="text-xs text-text-muted">
              From
            </Label>
            <Input id={ids.from} type="date" className="h-8" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div className="w-36 space-y-1">
            <Label htmlFor={ids.to} className="text-xs text-text-muted">
              To
            </Label>
            <Input id={ids.to} type="date" className="h-8" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          <Button type="submit" variant="outline" size="sm">
            Apply
          </Button>
        </form>
      </div>
      {problem ? (
        <p className="flex gap-1 text-xs text-text" role="alert">
          <span aria-hidden="true" className="text-warning-signal">
            ●
          </span>
          {problem}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[13px]">
        <div className="flex flex-wrap items-center gap-1">
          <span className="mr-1 text-text-muted">Period:</span>
          {presets.map((p) => {
            const active = filters.from === p.from && filters.to === p.to;
            return (
              <Button
                key={p.id}
                type="button"
                size="sm"
                variant={active ? "default" : "ghost"}
                aria-pressed={active}
                className="h-7 px-2 font-sans text-[13px] font-medium"
                onClick={() => onChange({ ...filters, from: p.from, to: p.to })}
              >
                {p.label}
              </Button>
            );
          })}
        </div>
        <fieldset className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <legend className="float-left mr-2 text-text-muted">Types:</legend>
          {TRANSACTION_TYPES.map((t) => {
            const checked = filters.types.includes(t);
            return (
              <label key={t} className="inline-flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() =>
                    onChange({
                      ...filters,
                      subtypes: [],
                      types: checked ? filters.types.filter((x) => x !== t) : [...filters.types, t],
                    })
                  }
                  className="size-4 accent-accent"
                />
                {TYPE_LABELS[t]}
              </label>
            );
          })}
          <span className="text-xs text-text-faint">(none ticked shows every type)</span>
        </fieldset>
        {filters.subtypes.length && tab === null ? (
          <span className="text-xs text-text-muted">Subtype: {filters.subtypes.map(humanize).join(", ")}</span>
        ) : null}
        {filtered ? (
          <Button type="button" variant="ghost" size="sm" className="h-7 px-2 font-sans text-[13px] font-medium" onClick={() => onChange(EMPTY_TRANSACTION_FILTERS)}>
            <XIcon aria-hidden="true" />
            Clear filters
          </Button>
        ) : null}
      </div>
    </div>
  );
}

const BADGE: Record<string, string> = {
  BUY: "bg-accent text-accent-foreground",
  SELL: "bg-accent-soft text-accent-ink",
  dividend: "bg-gain-subtle text-gain",
};

function badge(r: TransactionOut): { text: string; cls: string } {
  if (r.type === "dividend") return { text: "Dividend", cls: BADGE.dividend };
  if (r.type === "order" && (r.subtype === "BUY" || r.subtype === "SELL")) {
    return { text: r.subtype === "BUY" ? "Buy" : "Sell", cls: BADGE[r.subtype] };
  }
  return { text: r.subtype ? humanize(r.subtype) : TYPE_SINGULAR[r.type], cls: "bg-border-soft text-text" };
}

function TransactionTable({ rows, busy }: { rows: TransactionOut[]; busy: boolean }) {
  // The size bar compares each amount with the largest on this page in the same currency.
  const largest = new Map<string, number>();
  for (const r of rows) {
    const k = r.currency ?? "";
    largest.set(k, Math.max(largest.get(k) ?? 0, Math.abs(r.amount)));
  }
  return (
    <div className={cn("overflow-x-auto", busy && "opacity-60")}>
      <table className="w-full min-w-[56rem] text-sm">
        <caption className="sr-only">Transactions, newest first</caption>
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <th scope="col" className="px-5 py-2.5 font-medium">Date</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Type</th>
            <th scope="col" className="px-3 py-2.5 font-medium">Instrument</th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">Quantity</th>
            <th scope="col" className="px-3 py-2.5 text-right font-medium">
              Price <span className="sr-only">(instrument currency)</span>
            </th>
            <th scope="col" className="w-[18%] px-3 py-2.5 font-medium">
              <span title="Each amount against the largest on this page (same currency)">Relative size</span>
            </th>
            <th scope="col" className="px-5 py-2.5 text-right font-medium">Amount</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const b = badge(r);
            const max = largest.get(r.currency ?? "") ?? 0;
            const at = new Date(r.occurred_at);
            return (
              <tr key={r.id} className="border-b border-border-soft last:border-0 hover:bg-border-soft">
                <td className="num px-5 py-2.5 whitespace-nowrap">
                  <time dateTime={r.occurred_at} className="block font-mono text-xs text-text-muted">
                    {formatDate(at)}
                  </time>
                  <span className="block font-mono text-[11px] text-text-faint">{formatTime(at)}</span>
                </td>
                <th scope="row" className="px-3 py-2.5 text-left font-normal">
                  <span className={cn("inline-block px-2 py-0.5 text-[11px] font-semibold", b.cls)}>{b.text}</span>
                  <span className="mt-0.5 block text-[11px] text-text-muted">
                    {TYPE_SINGULAR[r.type]}
                    {r.subtype ? ` · ${humanize(r.subtype)}` : null}
                  </span>
                </th>
                <td className="px-3 py-2.5">
                  {r.ticker ? (
                    <Link href={`/holdings/${encodeURIComponent(r.ticker)}`} className="group inline-flex flex-wrap items-baseline gap-x-1.5">
                      <span className="font-mono text-xs font-medium text-text" title={r.ticker}>
                        {shortTicker(r.ticker)}
                      </span>
                      <span className="text-[13px] text-text-muted group-hover:text-accent-ink group-hover:underline">{r.name ?? r.ticker}</span>
                    </Link>
                  ) : (
                    <span className="inline-flex items-baseline gap-1.5">
                      <span className="font-mono text-xs font-medium">{r.currency ?? "—"}</span>
                      <span className="text-[13px] text-text-muted">{r.type === "cash" ? humanize(r.subtype) : "—"}</span>
                    </span>
                  )}
                </td>
                <td className="num px-3 py-2.5 text-right">{formatQuantity(r.quantity)}</td>
                <td className="num px-3 py-2.5 text-right text-text-muted">{price(r.price)}</td>
                <td className="px-3 py-2.5" aria-hidden="true">
                  <div className="h-1.5 bg-border-soft">
                    <div className="h-full bg-accent/55" style={{ width: `${max > 0 ? (Math.abs(r.amount) / max) * 100 : 0}%` }} />
                  </div>
                </td>
                <td className={cn("num px-5 py-2.5 text-right font-semibold whitespace-nowrap", r.amount > 0 && "text-gain")}>
                  {money(r.amount, r.currency, true)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Dividends({ filters, dividends }: { filters: TransactionFilters; dividends: TotalByCurrency[] }) {
  const excluded = filters.types.length > 0 && !filters.types.includes("dividend");
  return (
    <Blueprint aria-labelledby="dividends-heading" className="flex min-w-0 flex-col gap-3 px-5.5 py-5">
      <PanelHeading
        kicker="Dividends · table filters"
        title={
          <span id="dividends-heading">
            Dividends, {periodLabel(filters)}
            {filters.ticker ? <span className="font-normal text-text-muted"> · {filters.ticker}</span> : null}
          </span>
        }
      />
      {excluded ? (
        <p className="text-sm text-text-muted">Dividends are not among the selected types.</p>
      ) : dividends.length === 0 ? (
        <p className="text-sm text-text-muted">No dividends in this period.</p>
      ) : (
        <dl className="grid gap-4 sm:grid-cols-2">
          {dividends.map((d) => (
            <div key={d.currency ?? "none"} className="min-w-0">
              <dt className="text-xs text-text-muted">Dividends{d.currency ? ` (${d.currency})` : ""}</dt>
              <dd className="num font-heading text-[28px] leading-tight font-semibold">{money(d.amount, d.currency)}</dd>
              <dd className="text-xs text-text-muted">
                {d.count} payment{d.count === 1 ? "" : "s"}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </Blueprint>
  );
}

function Totals({ totals, count }: { totals: TotalByCurrency[]; count: number }) {
  return (
    <Blueprint aria-labelledby="totals-heading" className="flex min-w-0 flex-col gap-3 px-5.5 py-5">
      <PanelHeading
        kicker="Totals · table filters"
        title={
          <span id="totals-heading">
            Totals <span className="num text-sm font-normal text-text-muted">({count} transactions)</span>
          </span>
        }
      />
      {totals.length === 0 ? (
        <p className="text-sm text-text-muted">No transactions match these filters.</p>
      ) : (
        <div className="-mx-5.5 overflow-x-auto">
          <table className="w-full min-w-[32rem] text-sm">
            <caption className="sr-only">Count and sum of amounts per type, subtype and currency over every matching transaction</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
                <th scope="col" className="px-5.5 py-2 text-left font-medium">Type</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Subtype</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Currency</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Count</th>
                <th scope="col" className="px-5.5 py-2 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody>
              {totals.map((t) => (
                <tr key={`${t.type}:${t.subtype}:${t.currency}`} className="border-b border-border-soft last:border-0">
                  <th scope="row" className="px-5.5 py-2 text-left font-normal">{TYPE_LABELS[t.type]}</th>
                  <td className="px-3 py-2 text-text-muted">{humanize(t.subtype)}</td>
                  <td className="px-3 py-2 font-mono text-xs">{t.currency ?? "—"}</td>
                  <td className="num px-3 py-2 text-right">{t.count}</td>
                  <td className="num px-5.5 py-2 text-right">{money(t.amount, t.currency, true)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Blueprint>
  );
}
