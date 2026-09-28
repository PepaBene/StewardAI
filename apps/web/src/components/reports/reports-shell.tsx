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

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Oversight"
        title="Reports"
        description="Daily and weekly digests and monthly reviews. Every claim cites its source; a day with nothing material says so."
        aside={aside}
      />
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
              {items.map((item) => {
                const selected = item.id === selectedId;
                const spark = history ? historyUpTo(history, localDay(item.at)) : [];
                return (
                  <li key={item.id} className="border-b border-border-soft last:border-0">
                    <Link
                      href={item.href}
                      aria-current={selected ? "page" : undefined}
                      className={cn(
                        "block border-l-2 px-4 py-3.5 hover:bg-border-soft",
                        selected ? "border-l-accent bg-accent-soft" : "border-l-transparent",
                      )}
                    >
                      <span className="flex items-baseline justify-between gap-2 text-xs">
                        <span className="font-semibold text-accent-ink">{item.kind}</span>
                        <span className="num text-text-muted">{item.dateLabel}</span>
                      </span>
                      <span className="mt-1 block text-[15px] leading-snug font-medium text-text">{item.title}</span>
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
              })}
            </ul>
          ) : null}
          {items && items.length > 0 ? (
            <p className="border-t border-border-soft px-4 py-2 text-[11px] text-text-faint">
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
        <span key={channel} className="border border-border px-1.5 py-px text-[11px] text-text-muted" title={d.at ? `at ${d.at}` : undefined}>
          {channel}: {d.status ?? "—"}
        </span>
      ))}
    </>
  );
}
