"use client";

import { LoaderCircleIcon } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Sparkline } from "@/components/charts";
import { ReportsTabs, type ReportsTab } from "@/components/news/reports-tabs";
import { PageHeader } from "@/components/page-header";
import { ReportSearch } from "@/components/reports/report-search";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { getBrowserApiClient, type PortfolioHistory } from "@/lib/api/client";
import { historyUpTo, localDay, type DatedClose } from "@/lib/reports/digest-overview";
import { searchFromParams } from "@/lib/reports/search";
import { cn } from "@/lib/utils";

export type ShellItem = {
  id: number;
  href: string;
  /** "Daily", "Weekly", "Monthly". */
  kind: string;
  /** Right-aligned date label. */
  dateLabel: string;
  /** Instant the report was written: its sparkline runs up to this day. */
  at: string;
  title: string;
  /** Muted line under the title, e.g. "4 items · 16 citations". */
  meta?: ReactNode;
  /** Small trailing chips (delivery per channel). */
  chips?: ReactNode;
  /** A "nothing material" report: runs of these fold into one line. */
  quiet?: boolean;
};

type ListEntry = { type: "item"; item: ShellItem } | { type: "quiet"; items: ShellItem[] };

/** Consecutive quiet reports (two or more) fold into one entry; a selected one stays visible. */
export function foldQuiet(items: readonly ShellItem[], selectedId: number | null): ListEntry[] {
  const out: ListEntry[] = [];
  let run: ShellItem[] = [];
  const flush = () => {
    if (run.length >= 2) out.push({ type: "quiet", items: run });
    else run.forEach((item) => out.push({ type: "item", item }));
    run = [];
  };
  for (const item of items) {
    if (item.quiet && item.id !== selectedId) run.push(item);
    else {
      flush();
      out.push({ type: "item", item });
    }
  }
  flush();
  return out;
}

const PAGE_TITLES: Partial<Record<ReportsTab, { title: string; description: string }>> = {
  reviews: {
    title: "Monthly reviews",
    description: "A look back at each month: performance, what changed, your theses and next steps.",
  },
};
const DEFAULT_TITLE = {
  title: "Daily and weekly",
  description: "The digests written for you. Every claim cites its source; a day with nothing material says so.",
};

const HISTORY_DAYS = 400;
const HISTORY_TTL_MS = 5 * 60_000;
let historyCache: { at: number; promise: Promise<PortfolioHistory | null> } | null = null;

/**
 * The portfolio history (`/portfolio/history`), fetched once and shared by the list's
 * sparklines and the digest hero (cached for five minutes). Undefined while loading.
 */
export function usePortfolioHistory(): PortfolioHistory | null | undefined {
  const [history, setHistory] = useState<PortfolioHistory | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    if (!historyCache || Date.now() - historyCache.at > HISTORY_TTL_MS) {
      historyCache = {
        at: Date.now(),
        promise: getBrowserApiClient()
          .GET("/portfolio/history", { params: { query: { days: HISTORY_DAYS } } })
          .then(({ data }) => data ?? null)
          .catch(() => null),
      };
    }
    void historyCache.promise.then((h) => {
      if (!cancelled) setHistory(h);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return history;
}

/** The flow-adjusted portfolio index points, or null while loading. */
export function useIndexHistory(): DatedClose[] | null {
  const history = usePortfolioHistory();
  return history === undefined ? null : (history?.points ?? []);
}

/**
 * The Reports layout from the design: the page header, full-text search, then a list panel
 * (type tabs over the reports of that type, each with the portfolio index up to its date)
 * beside the selected report.
 */
export function ReportsShell({
  tab,
  items,
  error,
  selectedId,
  aside,
  notices,
  emptyList,
  children,
}: {
  tab: ReportsTab;
  /** Null while loading. */
  items: ShellItem[] | null;
  error?: string | null;
  selectedId: number | null;
  /** Header actions and freshness. */
  aside?: ReactNode;
  /** Queued-job notices under the header. */
  notices?: ReactNode;
  /** Shown in the list panel when there are no items. */
  emptyList?: ReactNode;
  children?: ReactNode;
}) {
  const params = useSearchParams();
  const search = searchFromParams(params);
  const history = useIndexHistory();
  const heading = PAGE_TITLES[tab] ?? DEFAULT_TITLE;

  const renderItem = (item: ShellItem) => {
    const selected = item.id === selectedId;
    const spark = history ? historyUpTo(history, localDay(item.at)) : [];
    return (
      <li key={item.id} className="border-b border-border-soft last:border-0">
        <Link
          href={item.href}
          aria-current={selected ? "page" : undefined}
          className={cn("relative block px-4 py-3.5 hover:bg-surface-muted", selected && "bg-surface-muted")}
        >
          {selected ? <span aria-hidden="true" className="absolute top-3 bottom-3 left-0 w-[3px] rounded-r-sm bg-accent" /> : null}
          <span className="flex items-baseline justify-between gap-2 text-xs">
            <span className="font-medium text-text-muted">{item.kind}</span>
            <span className="num text-text-muted">{item.dateLabel}</span>
          </span>
          <span className={cn("mt-1 line-clamp-3 block text-[15px] leading-snug", item.quiet ? "text-text-muted" : "font-medium text-text")}>
            {item.title}
          </span>
          <span className="mt-1 flex items-end justify-between gap-3">
            <span className="min-w-0 text-xs text-text-muted">{item.meta}</span>
            <span className="w-14 shrink-0">
              <Sparkline values={spark} width={56} height={18} className="h-[18px]" />
            </span>
          </span>
          {item.chips ? <span className="mt-1.5 flex flex-wrap gap-1">{item.chips}</span> : null}
        </Link>
      </li>
    );
  };

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title={heading.title} description={heading.description} aside={aside} />
      {notices}
      <ReportSearch key={search.q} state={search} />

      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[21.5rem_minmax(0,1fr)]">
        <nav aria-label="Reports of this type" className="blueprint">
          <BlueprintCorners />
          <ReportsTabs active={tab} />
          {error ? (
            <div className="p-4">
              <AlertMessage title="Could not load reports">{error}</AlertMessage>
            </div>
          ) : null}
          {items === null && !error ? (
            <p className="flex items-center gap-2 p-4 text-sm text-text-muted" role="status">
              <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
              Loading…
            </p>
          ) : null}
          {items && items.length === 0 ? <div className="p-4 text-sm text-text-muted">{emptyList}</div> : null}
          {items && items.length > 0 ? (
            <ul className="m-0 max-h-[26rem] list-none overflow-y-auto p-0 lg:max-h-[70rem]">
              {foldQuiet(items, selectedId).map((entry) =>
                entry.type === "item" ? (
                  renderItem(entry.item)
                ) : (
                  <li key={`quiet-${entry.items[0].id}`} className="border-b border-border-soft last:border-0">
                    <details className="group">
                      <summary className="flex cursor-pointer list-none items-baseline justify-between gap-2 px-4 py-2.5 text-[13px] text-text-muted hover:bg-surface-muted">
                        <span>
                          {entry.items.length} quiet {entry.items[0].kind === "Weekly" ? "weeks" : "days"}, nothing material
                        </span>
                        <span className="num text-xs">
                          {entry.items[entry.items.length - 1].dateLabel} – {entry.items[0].dateLabel}
                        </span>
                      </summary>
                      <ul className="m-0 list-none border-t border-border-soft p-0">{entry.items.map(renderItem)}</ul>
                    </details>
                  </li>
                ),
              )}
            </ul>
          ) : null}
          {items && items.length > 0 ? (
            <p className="border-t border-border-soft px-4 py-2 text-xs text-text-faint">
              Sparkline: the flow-adjusted portfolio index for the 30 days up to each report.
            </p>
          ) : null}
        </nav>
        <div className="min-w-0">{children}</div>
      </div>
    </div>
  );
}

/** A report's delivery per channel: "telegram: sent". */
export function DeliveryChips({ delivery }: { delivery: Record<string, Record<string, string>> }) {
  const entries = Object.entries(delivery);
  if (entries.length === 0) return null;
  return (
    <>
      {entries.map(([channel, d]) => (
        <span key={channel} className="rounded-full bg-surface-muted px-2 py-px text-xs text-text-muted" title={d.at ? `at ${d.at}` : undefined}>
          {channel}: {d.status ?? "—"}
        </span>
      ))}
    </>
  );
}
