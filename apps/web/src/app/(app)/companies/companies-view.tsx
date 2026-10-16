"use client";

import { ChevronRightIcon, SearchIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { PageHeader } from "@/components/page-header";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatPercent } from "@/lib/format";
import { shortTicker } from "@/lib/portfolio/ticker";
import { cn } from "@/lib/utils";

type Holding = components["schemas"]["Holding"];

const isFund = (h: Holding) => /etf|fund/i.test(h.security_type ?? "");


/**
 * Companies landing (plan §3.1): every company and fund you hold, largest first, each
 * opening its report. Search narrows by name or symbol.
 */
export function CompaniesView() {
  const [rows, setRows] = useState<Holding[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    getBrowserApiClient()
      .GET("/portfolio/holdings")
      .then(({ data, response }) => {
        if (cancelled) return;
        if (data) setRows([...data.holdings].sort((a, b) => b.value_base - a.value_base));
        else setError(`Couldn't load your holdings (HTTP ${response.status}).`);
      })
      .catch(() => !cancelled && setError("Couldn't reach the API. Check that the backend is running."));
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!rows) return null;
    if (!q) return rows;
    return rows.filter((h) => `${h.name ?? ""} ${h.ticker} ${h.isin ?? ""}`.toLowerCase().includes(q));
  }, [rows, query]);

  const companies = filtered?.filter((h) => !isFund(h)) ?? null;
  const funds = filtered?.filter(isFund) ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        kicker="Companies"
        title="Your companies and funds"
        description="Open any holding for its full report: valuation, growth, health, dividends and your thesis."
      />
      <div className="relative max-w-md">
        <SearchIcon aria-hidden="true" className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-text-faint" />
        <Input
          id="companies-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name, symbol or ISIN"
          className="pl-9"
          aria-label="Search companies and funds"
        />
      </div>
      {error ? (
        <AlertMessage tone="warning" title="Holdings unavailable">
          {error}
        </AlertMessage>
      ) : null}
      <div className="grid gap-6 lg:grid-cols-2 lg:items-start">
        <EntityList
          title="Companies"
          rows={companies}
          href={(h) => `/companies/${encodeURIComponent(h.ticker)}`}
          empty={query ? "No company matches that search." : "You hold no individual companies."}
        />
        <EntityList
          title="Funds"
          rows={funds}
          href={(h) => (h.isin ? `/funds/${encodeURIComponent(h.isin)}` : `/companies/${encodeURIComponent(h.ticker)}`)}
          empty={query ? "No fund matches that search." : "You hold no funds."}
          footer={
            <Link href="/funds/research" className="text-[13px] font-medium text-accent-ink hover:underline">
              Find a fund
            </Link>
          }
        />
      </div>
    </div>
  );
}

function EntityList({
  title,
  rows,
  href,
  empty,
  footer,
}: {
  title: string;
  rows: Holding[] | null;
  href: (h: Holding) => string;
  empty: string;
  footer?: React.ReactNode;
}) {
  return (
    <section className="blueprint p-5 md:p-6" aria-labelledby={`list-${title}`}>
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2 id={`list-${title}`} className="panel-title">
          {title}
        </h2>
        {rows ? <span className="text-xs text-text-muted num">{rows.length}</span> : null}
      </div>
      {rows === null ? (
        <ul aria-hidden="true" className="space-y-2">
          {Array.from({ length: 5 }, (_, i) => (
            <li key={i} className="h-12 animate-pulse rounded-lg bg-surface-muted" />
          ))}
        </ul>
      ) : rows.length === 0 ? (
        <p className="py-6 text-sm text-text-muted">{empty}</p>
      ) : (
        <ul className="divide-y divide-border-soft">
          {rows.map((h) => (
            <li key={h.ticker}>
              <Link
                href={href(h)}
                className="-mx-2 grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-3 rounded-lg px-2 py-3 hover:bg-surface-muted"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-text">{h.name ?? shortTicker(h.ticker)}</span>
                  <span className="mt-0.5 inline-block rounded-sm bg-surface-muted px-1.5 text-xs text-text-muted">
                    {shortTicker(h.ticker)}
                  </span>
                </span>
                <span className="text-right text-[13px] num">
                  <span className="block text-text">{formatPercent(h.weight)}</span>
                  <span
                    className={cn(
                      "block text-xs",
                      h.ppl_pct == null ? "text-text-faint" : h.ppl_pct >= 0 ? "text-gain" : "text-loss",
                    )}
                  >
                    {h.ppl_pct == null ? "—" : `${h.ppl_pct >= 0 ? "▲" : "▼"} ${formatPercent(h.ppl_pct, { signed: true })}`}
                  </span>
                </span>
                <ChevronRightIcon aria-hidden="true" className="size-4 text-text-faint" />
              </Link>
            </li>
          ))}
        </ul>
      )}
      {footer ? <div className="mt-3">{footer}</div> : null}
    </section>
  );
}
