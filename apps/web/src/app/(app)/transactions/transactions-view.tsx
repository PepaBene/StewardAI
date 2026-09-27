"use client";

import { LoaderCircleIcon, RefreshCwIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { HoldingsTabs } from "@/components/portfolio/holdings-tabs";
import { StatCard } from "@/components/portfolio/stat-card";
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
import { formatDate, formatDateTime, formatMoney, formatQuantity } from "@/lib/format";
import {
  appendUnique,
  EMPTY_TRANSACTION_FILTERS,
  isoDate,
  isTicker,
  parseTransactionFilters,
  periodPresets,
  transactionFiltersToSearch,
  transactionQuery,
  TRANSACTION_TYPES,
  TYPE_LABELS,
  TYPE_SINGULAR,
  type TransactionFilters,
} from "@/lib/portfolio/transactions";
import { cn } from "@/lib/utils";

const PAGE = 100;

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; page: TransactionsPage; items: TransactionOut[]; loadingMore: boolean; moreError: string | null };

async function fetchPage(filters: TransactionFilters, cursor: string | null): Promise<TransactionsPage> {
  const { data, error, response } = await getBrowserApiClient().GET("/portfolio/transactions", {
    params: { query: transactionQuery(filters, cursor, PAGE) },
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
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  // Only the newest request's answer is applied (filters can change while a page loads).
  const sequence = useRef(0);

  const setFilters = useCallback(
    (next: TransactionFilters) => router.replace(`${pathname}${transactionFiltersToSearch(next)}`, { scroll: false }),
    [router, pathname],
  );

  useEffect(() => {
    const mine = ++sequence.current;
    setLoad({ kind: "loading" });
    fetchPage(filters, null)
      .then((page) => {
        if (mine === sequence.current) {
          setLoad({ kind: "ok", page, items: appendUnique([], page.items), loadingMore: false, moreError: null });
        }
      })
      .catch((err: unknown) => {
        if (mine === sequence.current) setLoad({ kind: "error", message: message(err) });
      });
  }, [filters, reloadKey]);

  useEffect(() => {
    getBrowserApiClient()
      .GET("/portfolio/holdings")
      .then((r) => r.data && setHoldings(r.data.holdings))
      .catch(() => undefined);
  }, []);

  async function loadMore() {
    if (load.kind !== "ok" || !load.page.next_cursor || load.loadingMore) return;
    const mine = sequence.current;
    const cursor = load.page.next_cursor;
    setLoad({ ...load, loadingMore: true, moreError: null });
    try {
      const page = await fetchPage(filters, cursor);
      if (mine !== sequence.current) return;
      setLoad((prev) =>
        prev.kind === "ok" ? { kind: "ok", page, items: appendUnique(prev.items, page.items), loadingMore: false, moreError: null } : prev,
      );
    } catch (err) {
      if (mine !== sequence.current) return;
      setLoad((prev) => (prev.kind === "ok" ? { ...prev, loadingMore: false, moreError: message(err) } : prev));
    }
  }

  const filtered =
    filters.types.length > 0 || filters.ticker !== null || filters.from !== null || filters.to !== null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="page-title">Transactions and dividends</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Orders, dividends and cash movements from Trading212, newest first. Totals cover every
            matching row, not just the ones loaded below. Dates are UTC days, both ends included.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
          <RefreshCwIcon aria-hidden="true" />
          Reload
        </Button>
      </div>
      <HoldingsTabs />

      <Filters key={search} filters={filters} holdings={holdings} onChange={setFilters} filtered={filtered} />

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading transactions…
        </p>
      ) : null}
      {load.kind === "error" ? (
        <AlertMessage title="Could not load the transactions">
          <p>{load.message}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => setReloadKey((k) => k + 1)}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}

      {load.kind === "ok" ? (
        <>
          <Dividends filters={filters} dividends={load.page.dividends} />
          <Totals totals={load.page.totals} count={load.page.total_count} />
          <section aria-labelledby="rows-heading" className="space-y-3">
            <h2 id="rows-heading" className="text-[22px] leading-tight font-semibold">
              Transactions
            </h2>
            {load.items.length === 0 ? (
              <EmptyState title={filtered ? "No transactions match these filters" : "No transactions yet"}>
                {filtered ? "Widen the period or clear a filter." : "The portfolio sync imports orders, dividends and cash movements from Trading212."}
              </EmptyState>
            ) : (
              <TransactionTable rows={load.items} />
            )}
            <div className="flex flex-wrap items-center gap-3">
              <p className="num text-sm text-text-muted" role="status">
                Showing {load.items.length} of {load.page.total_count}
              </p>
              {load.page.next_cursor ? (
                <Button variant="outline" size="sm" onClick={() => void loadMore()} disabled={load.loadingMore}>
                  {load.loadingMore ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
                  Load more
                </Button>
              ) : null}
              {load.moreError ? <span className="text-sm text-text">Could not load more: {load.moreError}</span> : null}
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}

function Filters({
  filters,
  holdings,
  onChange,
  filtered,
}: {
  filters: TransactionFilters;
  holdings: Holding[];
  onChange: (next: TransactionFilters) => void;
  filtered: boolean;
}) {
  const ids = { ticker: useId(), from: useId(), to: useId(), list: useId() };
  const [ticker, setTicker] = useState(filters.ticker ?? "");
  const [from, setFrom] = useState(filters.from ?? "");
  const [to, setTo] = useState(filters.to ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const presets = useMemo(() => periodPresets(), []);

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
    <section aria-label="Filters" className="space-y-3 rounded-lg border border-border bg-surface-raised p-4">
      <fieldset className="space-y-1">
        <legend className="mb-1 text-sm font-medium">Type</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {TRANSACTION_TYPES.map((t) => {
            const checked = filters.types.includes(t);
            return (
              <label key={t} className="inline-flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() =>
                    onChange({ ...filters, types: checked ? filters.types.filter((x) => x !== t) : [...filters.types, t] })
                  }
                  className="size-4 accent-accent"
                />
                {TYPE_LABELS[t]}
              </label>
            );
          })}
        </div>
        <p className="text-xs text-text-muted">None ticked shows every type.</p>
      </fieldset>

      <form onSubmit={apply} className="flex flex-wrap items-end gap-3">
        <div className="w-48 space-y-1">
          <Label htmlFor={ids.ticker}>Ticker</Label>
          <Input id={ids.ticker} value={ticker} list={ids.list} spellCheck={false} placeholder="Any" onChange={(e) => setTicker(e.target.value)} />
          <datalist id={ids.list}>
            {holdings.map((h) => (
              <option key={h.ticker} value={h.ticker}>
                {h.name ?? h.ticker}
              </option>
            ))}
          </datalist>
        </div>
        <div className="w-44 space-y-1">
          <Label htmlFor={ids.from}>From</Label>
          <Input id={ids.from} type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="w-44 space-y-1">
          <Label htmlFor={ids.to}>To</Label>
          <Input id={ids.to} type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <Button type="submit" variant="outline">
          Apply
        </Button>
        {filtered ? (
          <Button type="button" variant="ghost" onClick={() => onChange(EMPTY_TRANSACTION_FILTERS)}>
            <XIcon aria-hidden="true" />
            Clear filters
          </Button>
        ) : null}
      </form>
      {problem ? (
        <p className="flex gap-1 text-xs text-text" role="alert">
          <span aria-hidden="true" className="text-warning-signal">
            ●
          </span>
          {problem}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-text-muted">Period:</span>
        {presets.map((p) => {
          const active = filters.from === p.from && filters.to === p.to;
          return (
            <Button
              key={p.id}
              type="button"
              size="sm"
              variant={active ? "default" : "ghost"}
              aria-pressed={active}
              onClick={() => onChange({ ...filters, from: p.from, to: p.to })}
            >
              {p.label}
            </Button>
          );
        })}
      </div>
    </section>
  );
}

function Dividends({ filters, dividends }: { filters: TransactionFilters; dividends: TotalByCurrency[] }) {
  const excluded = filters.types.length > 0 && !filters.types.includes("dividend");
  return (
    <section aria-labelledby="dividends-heading" className="space-y-3">
      <h2 id="dividends-heading" className="text-[22px] leading-tight font-semibold">
        Dividends, {periodLabel(filters)}
        {filters.ticker ? <span className="font-normal text-text-muted"> · {filters.ticker}</span> : null}
      </h2>
      {excluded ? (
        <p className="text-sm text-text-muted">Dividends are not among the selected types.</p>
      ) : dividends.length === 0 ? (
        <p className="text-sm text-text-muted">No dividends in this period.</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {dividends.map((d) => (
            <StatCard
              key={d.currency ?? "none"}
              label={`Dividends${d.currency ? ` (${d.currency})` : ""}`}
              value={money(d.amount, d.currency)}
              sub={`${d.count} payment${d.count === 1 ? "" : "s"}`}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function Totals({ totals, count }: { totals: TotalByCurrency[]; count: number }) {
  if (totals.length === 0) return null;
  return (
    <section aria-labelledby="totals-heading" className="space-y-3">
      <h2 id="totals-heading" className="text-[22px] leading-tight font-semibold">
        Totals <span className="num text-sm font-normal text-text-muted">({count} transactions)</span>
      </h2>
      <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
        <table className="w-full min-w-[32rem] text-sm">
          <caption className="sr-only">Count and sum of amounts per type, subtype and currency over every matching transaction</caption>
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-text-muted">
              <th scope="col" className="px-3 py-2 text-left font-medium">Type</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Subtype</th>
              <th scope="col" className="px-3 py-2 text-left font-medium">Currency</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Count</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody>
            {totals.map((t) => (
              <tr key={`${t.type}:${t.subtype}:${t.currency}`} className="border-b border-border last:border-0">
                <th scope="row" className="px-3 py-2 text-left font-normal">{TYPE_LABELS[t.type]}</th>
                <td className="px-3 py-2 text-text-muted">{humanize(t.subtype)}</td>
                <td className="px-3 py-2 font-mono text-xs">{t.currency ?? "—"}</td>
                <td className="num px-3 py-2 text-right">{t.count}</td>
                <td className="num px-3 py-2 text-right">{money(t.amount, t.currency, true)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TransactionTable({ rows }: { rows: TransactionOut[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
      <table className="w-full min-w-[48rem] text-sm">
        <caption className="sr-only">Transactions, newest first</caption>
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-text-muted">
            <th scope="col" className="px-3 py-2 text-left font-medium">Date</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Type</th>
            <th scope="col" className="px-3 py-2 text-left font-medium">Holding</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Quantity</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Price <span className="sr-only">(instrument currency)</span>
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Amount</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-b border-border last:border-0">
              <td className="num px-3 py-2 whitespace-nowrap text-text-muted">{formatDateTime(r.occurred_at)}</td>
              <th scope="row" className="px-3 py-2 text-left font-normal">
                <span className="block">{TYPE_SINGULAR[r.type]}</span>
                {r.subtype ? <span className="block text-xs text-text-muted">{humanize(r.subtype)}</span> : null}
              </th>
              <td className="px-3 py-2">
                {r.ticker ? (
                  <Link href={`/holdings/${encodeURIComponent(r.ticker)}`} className="group block">
                    <span className={cn("block text-text group-hover:text-accent group-hover:underline")}>{r.name ?? r.ticker}</span>
                    <span className="block font-mono text-xs text-text-muted">{r.ticker}</span>
                  </Link>
                ) : (
                  <span className="text-text-muted">—</span>
                )}
              </td>
              <td className="num px-3 py-2 text-right">{formatQuantity(r.quantity)}</td>
              <td className="num px-3 py-2 text-right">{price(r.price)}</td>
              <td className="num px-3 py-2 text-right">{money(r.amount, r.currency, true)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
