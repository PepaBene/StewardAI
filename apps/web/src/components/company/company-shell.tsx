"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, type ReactNode } from "react";

import { Blueprint } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { companyHref, TABS, toNumber, type Overview } from "@/lib/company/company";
import { formatDate } from "@/lib/format";
import { useApiResource, type ResourceState } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const OverviewContext = createContext<ResourceState<Overview>>({ kind: "loading" });

/** The company's overview, fetched once by the shell and shared by the header and the Overview tab. */
export function useCompanyOverview(): ResourceState<Overview> {
  return useContext(OverviewContext);
}

function Header({ state, ticker }: { state: ResourceState<Overview>; ticker: string }) {
  const o = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const close = toNumber(o?.last_close);
  return (
    <Blueprint className="px-[22px] py-5">
      <div className="flex flex-wrap items-start justify-between gap-x-8 gap-y-3">
        <div className="min-w-0">
          <div className="kicker">Company workbench</div>
          <h1 className="page-title mt-1 mb-1">{o?.name ?? o?.legal_name ?? ticker}</h1>
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-text-muted">
            <span className="font-mono text-text">{ticker}</span>
            {o?.isin ? <span className="num">{o.isin}</span> : null}
            {o?.country ? <span>{o.country}</span> : null}
            {o?.status && o.status !== "active" ? <span className="border border-border px-1.5 text-xs text-unconfirmed">{o.status}</span> : null}
            <Link href={`/holdings/${encodeURIComponent(ticker)}`} className="text-accent-ink hover:underline">
              Position and thesis
            </Link>
          </p>
        </div>
        <dl className="flex flex-wrap gap-x-8 gap-y-2">
          <div className="border-l border-border pl-3">
            <dt className="text-xs text-text-muted">Last close{o?.last_close_date ? ` · ${formatDate(o.last_close_date)}` : ""}</dt>
            <dd className="num text-lg font-semibold">
              {close !== null ? `${close.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${o?.currency ?? ""}` : <span className="text-sm font-normal text-text-muted">no stored close</span>}
            </dd>
          </div>
          {o ? (
            <div className="border-l border-border pl-3">
              <dt className="text-xs text-text-muted">Data coverage</dt>
              <dd className="num text-lg font-semibold">
                {o.coverage_score.have} of {o.coverage_score.of}
                <span className="ml-1 text-sm font-normal text-text-muted">areas</span>
              </dd>
            </div>
          ) : null}
        </dl>
      </div>
      {o && o.listings.length > 1 ? (
        <p className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3 text-sm">
          <span className="text-text-muted">Other listings of this issuer:</span>
          {o.listings.map((l) =>
            l.selected ? (
              <span key={l.ticker} className="border border-accent px-2 py-0.5 font-mono text-xs" aria-current="true">
                {l.ticker} {l.currency}
              </span>
            ) : (
              <Link key={l.ticker} href={companyHref(l.ticker)} className="border border-border px-2 py-0.5 font-mono text-xs hover:border-accent">
                {l.ticker} {l.currency}
              </Link>
            ),
          )}
        </p>
      ) : null}
    </Blueprint>
  );
}

function Tabs({ ticker }: { ticker: string }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Company sections" className="-mx-1 overflow-x-auto">
      <ul className="flex min-w-max gap-1 border-b border-border px-1">
        {TABS.map((t) => {
          const href = companyHref(ticker, t.slug);
          const active = t.slug === "" ? pathname === href : pathname.startsWith(href);
          return (
            <li key={t.slug}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "-mb-px block border-b-2 px-3.5 py-2 text-sm whitespace-nowrap focus-visible:ring-2 focus-visible:ring-focus/30 focus-visible:outline-none",
                  active ? "border-accent font-semibold text-text" : "border-transparent text-text-muted hover:text-text",
                )}
              >
                {t.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function CompanyShell({ ticker, children }: { ticker: string; children: ReactNode }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/companies/{ticker}/overview", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state } = useApiResource(fetcher);
  return (
    <OverviewContext.Provider value={state}>
      <div className="flex flex-col gap-5">
        <Header state={state} ticker={ticker} />
        <Tabs ticker={ticker} />
        {children}
      </div>
    </OverviewContext.Provider>
  );
}
