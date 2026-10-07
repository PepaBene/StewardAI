"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, type ReactNode } from "react";

import { Blueprint } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { FUND_TABS, fundHref, lagDays, type FundProfile } from "@/lib/fundresearch/fund-research";
import { useApiResource, type ResourceState } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const ProfileContext = createContext<ResourceState<FundProfile>>({ kind: "loading" });

/** The fund's profile, fetched once by the shell and shared by the header and the Profile tab. */
export function useFundProfile(): ResourceState<FundProfile> {
  return useContext(ProfileContext);
}

function Header({ state, isin }: { state: ResourceState<FundProfile>; isin: string }) {
  const p = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const lag = lagDays(p?.holdings_as_of);
  return (
    <Blueprint className="px-[22px] py-5">
      <div className="flex flex-wrap items-start justify-between gap-x-8 gap-y-3">
        <div className="min-w-0">
          <div className="kicker">Fund research</div>
          <h1 className="page-title mt-1 mb-1">{p?.name ?? isin}</h1>
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-text-muted">
            <span className="font-mono text-text">{isin}</span>
            {p?.provider ? <span>{p.provider}</span> : null}
            {p?.identity.share_class ? <span>share class {p.identity.share_class}</span> : null}
            <Link href="/funds/research" className="text-accent-ink hover:underline">
              All funds
            </Link>
          </p>
        </div>
        {p ? (
          <dl className="flex flex-wrap gap-x-8 gap-y-2">
            <div className="border-l border-border pl-3">
              <dt className="text-xs text-text-muted">Holdings as of</dt>
              <dd className="num text-lg font-semibold">
                {p.holdings_as_of ? formatDate(p.holdings_as_of) : <span className="text-sm font-normal text-text-muted">none stored</span>}
              </dd>
              {lag !== null ? <p className={cn("text-[11px]", lag > 45 ? "text-unconfirmed" : "text-text-muted")}>{lag} days old</p> : null}
            </div>
            <div className="border-l border-border pl-3">
              <dt className="text-xs text-text-muted">Profile fields filled</dt>
              <dd className="num text-lg font-semibold">
                {p.have} of {p.of}
              </dd>
            </div>
          </dl>
        ) : null}
      </div>
      {p && p.identity.listings.length > 0 ? (
        <p className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3 text-sm">
          <span className="text-text-muted">Exchange listings of this share class:</span>
          {p.identity.listings.map((l) => (
            <span key={l.ticker} className="border border-border px-2 py-0.5 font-mono text-xs" title={l.name ?? undefined}>
              {l.ticker} {l.currency ?? ""}
              {l.has_prices ? "" : " (no prices)"}
            </span>
          ))}
        </p>
      ) : null}
    </Blueprint>
  );
}

function Tabs({ isin }: { isin: string }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Fund sections" className="-mx-1 overflow-x-auto">
      <ul className="flex min-w-max gap-1 border-b border-border px-1">
        {FUND_TABS.map((t) => {
          const href = fundHref(isin, t.slug);
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

export function FundShell({ isin, children }: { isin: string; children: ReactNode }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/funds/{isin}/profile", { params: { path: { isin } } }), [isin]);
  const { state } = useApiResource(fetcher);
  return (
    <ProfileContext.Provider value={state}>
      <div className="flex flex-col gap-5">
        <Header state={state} isin={isin} />
        <Tabs isin={isin} />
        {children}
      </div>
    </ProfileContext.Provider>
  );
}
