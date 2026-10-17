"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import { HoldingSectionPanels } from "@/app/(app)/holdings/[ticker]/holding-view";
import { openChatPanel } from "@/components/chat/chat-panel";
import { ScoreMark } from "@/components/insight/score-mark";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { companyHref, toNumber, type Overview } from "@/lib/company/company";
import { formatDate, formatMoney, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { useCompanyChecks } from "@/lib/insight/checks";
import { MARK_AXES, markPetals, num, petalsFromMap } from "@/lib/insight/insight";
import { shortTicker } from "@/lib/portfolio/ticker";
import { cn } from "@/lib/utils";

import { ChecksProvider, FairValueProvider, OverviewProvider, useFairValueReport, useShellChecks, type FairValueReport } from "./company-context";
import { EntityLayout, SectionSubNav, type EntitySectionLink } from "@/components/layout/entity-layout";
import { COMPANY_SECTIONS, isSectionHome, sectionForSlug, slugFromPath } from "./sections";
import { SectionHead } from "./section-head";

export { useCompanyOverview } from "./company-context";

function displayName(o: Overview | null, ticker: string): string {
  return o?.name ?? o?.legal_name ?? shortTicker(ticker);
}

type Summary = components["schemas"]["CompanySummary"];
type Position = NonNullable<Summary["position"]>;

function FairValueChip({ ticker, stored }: { ticker: string; stored: Summary["fair_value"] }) {
  const { report } = useFairValueReport();
  if (!report && stored) {
    // The last fair value the app stored, until Valuation computes a fresh one this visit.
    const upside = toNumber(stored.upside);
    return (
      <Link href={companyHref(ticker, "valuation")} className="rounded-control bg-surface-muted px-2.5 py-1.5 text-xs hover:ring-1 hover:ring-border">
        <span className="font-medium">
          Fair value {stored.currency ?? ""} {Number(stored.value).toLocaleString("en-GB", { maximumFractionDigits: 2 })}
        </span>
        {upside !== null ? <span className="text-text-muted">, price {formatPercent(Math.abs(upside), { decimals: 0 })} {upside < 0 ? "above" : "below"}</span> : null}
        <span className="block text-text-muted">
          {stored.basis_label}
          {stored.as_of ? `, ${formatDate(stored.as_of)}` : ""}
        </span>
      </Link>
    );
  }
  if (!report) {
    return (
      <Link href={companyHref(ticker, "valuation")} className="rounded-control border border-border px-2 py-1 text-xs text-accent-ink hover:underline">
        Fair value: open Valuation to compute it
      </Link>
    );
  }
  const gap = report.price !== null && report.value > 0 ? report.price / report.value - 1 : null;
  const currency = report.currency ?? "";
  return (
    <p className="rounded-control border border-border px-2 py-1 text-xs">
      <span className="font-medium">
        Fair value {currency} {report.value.toLocaleString("en-GB", { maximumFractionDigits: 2 })}
      </span>
      {gap !== null ? <span className="text-text-muted"> · price {formatPercent(Math.abs(gap), { decimals: 0 })} {gap > 0 ? "above" : "below"}</span> : null}
      <span className="block text-text-muted">{report.basis}</span>
    </p>
  );
}

/** The position card when the company is held: value, P/L and weight, from the company summary. */
function PositionCard({ holding }: { holding: Position }) {
  const pl = holding.ppl;
  return (
    <section aria-label="Your position" className="blueprint rounded-card px-3 py-2.5">
      <h3 className="text-xs text-text-muted">Your position</h3>
      <p className="kpi-figure num">{formatMoney(holding.value_account, holding.account_currency, { decimals: 0 })}</p>
      <dl className="mt-1 grid grid-cols-2 gap-x-3 text-xs">
        <div>
          <dt className="text-text-muted">Profit or loss</dt>
          <dd className={cn("num font-medium", pl > 0 ? "text-gain" : pl < 0 ? "text-loss" : "")}>
            {formatMoney(pl, holding.account_currency, { decimals: 0, signed: true })}
            {holding.ppl_pct !== null ? ` (${formatPercent(holding.ppl_pct, { decimals: 1, signed: true })})` : ""}
          </dd>
        </div>
        <div>
          <dt className="text-text-muted">Weight</dt>
          <dd className="num font-medium">{holding.weight !== null ? formatPercent(holding.weight, { decimals: 1 }) : "unknown"}</dd>
        </div>
      </dl>
    </section>
  );
}

function TickerSwitcher() {
  const router = useRouter();
  const [value, setValue] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const t = value.trim();
        if (t) router.push(companyHref(t));
      }}
    >
      <label className="sr-only" htmlFor="company-switcher">
        Open another company
      </label>
      <input
        id="company-switcher"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Open another company"
        className="w-full rounded-control border border-border bg-surface px-2.5 py-1.5 text-sm"
      />
    </form>
  );
}

function Actions({ ticker, name }: { ticker: string; name: string }) {
  return (
    <div className="flex flex-wrap gap-2">
      <Button type="button" size="sm" variant="outline" onClick={() => openChatPanel(`What should I know about ${name}?`)}>
        Ask about {shortTicker(ticker)}
      </Button>
      <Button asChild size="sm">
        <Link href={`${companyHref(ticker, "case")}#research`}>Start research</Link>
      </Button>
    </div>
  );
}

function PriceLine({ o, quote }: { o: Overview | null; quote: Summary["price"] | null }) {
  const close = toNumber(quote?.last) ?? toNumber(o?.last_close);
  if (close === null) return <p className="text-sm text-text-muted">{quote?.reason ?? "No stored close"}</p>;
  const pct = toNumber(quote?.day_change_pct);
  const date = quote?.as_of ?? o?.last_close_date;
  return (
    <p>
      <span className="kpi-figure num">{close.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>{" "}
      <span className="text-sm text-text-muted">{quote?.currency ?? o?.currency ?? ""}</span>
      <span className="block text-xs text-text-muted">
        {pct !== null ? (
          <span className={cn("num font-medium", pct > 0 ? "text-gain" : pct < 0 ? "text-loss" : "")}>{formatPercent(pct, { decimals: 2, signed: true })} on the day</span>
        ) : null}
        {pct !== null && date ? ", " : ""}
        {date ? `close ${formatDate(date)}` : ""}
      </span>
    </p>
  );
}

function Mark({ size, name, axes }: { size: "sidebar" | "header"; name: string; axes: Summary["axes"] | undefined }) {
  const checks = useShellChecks();
  // The full checks win once loaded; until then the summary's axis scores draw the same mark.
  if (checks.kind === "ok") return <ScoreMark petals={markPetals(checks.data.axes)} size={size} label={name} />;
  if (axes && axes.length > 0) return <ScoreMark petals={petalsFromMap(Object.fromEntries(axes.map((a) => [a.axis, a.score])), axes[0].max)} size={size} label={name} />;
  if (checks.kind === "loading") return <div aria-hidden="true" className={cn("rounded-mark bg-surface-muted", size === "sidebar" ? "size-[120px]" : "size-16")} />;
  // No checks and no summary: every axis drawn as unknown, never an empty box.
  return <ScoreMark petals={MARK_AXES.map((axis) => ({ axis, fraction: null, notApplicable: false }))} size={size} label={`${name}, not scored yet`} />;
}

function Sidebar({ o, ticker, summary }: { o: Overview | null; ticker: string; summary: Summary | null }) {
  const name = displayName(o, ticker);
  const holding = summary?.position ?? null;
  return (
    <>
      <TickerSwitcher />
      <Link href={companyHref(ticker, "checks")} aria-label="Open all checks" className="self-start">
        <Mark size="sidebar" name={name} axes={summary?.axes} />
      </Link>
      <div>
        <h1 className="page-title mb-1">{name}</h1>
        <p className="flex flex-wrap gap-1.5 text-xs">
          <span className="rounded-control bg-surface-muted px-1.5 py-0.5 font-medium">{shortTicker(ticker)}</span>
          {o?.country ? <span className="rounded-control bg-surface-muted px-1.5 py-0.5">{o.country}</span> : null}
          {o?.status && o.status !== "active" ? <span className="rounded-control bg-surface-muted px-1.5 py-0.5 text-unconfirmed">{o.status}</span> : null}
        </p>
      </div>
      <PriceLine o={o} quote={summary?.price ?? null} />
      <FairValueChip ticker={ticker} stored={summary?.fair_value ?? null} />
      <Actions ticker={ticker} name={name} />
      {holding ? <PositionCard holding={holding} /> : null}
    </>
  );
}

function CompactHeader({ o, ticker, summary }: { o: Overview | null; ticker: string; summary: Summary | null }) {
  const name = displayName(o, ticker);
  const holding = summary?.position ?? null;
  return (
    <div className="blueprint flex flex-col gap-3 rounded-card px-4 py-3">
      <div className="flex items-center gap-3">
        <Mark size="header" name={name} axes={summary?.axes} />
        <div className="min-w-0">
          <h1 className="page-title mb-0 truncate">{name}</h1>
          <p className="text-xs text-text-muted">
            <span className="font-medium text-text">{shortTicker(ticker)}</span>
            {o?.country ? `, ${o.country}` : ""}
          </p>
          <PriceLine o={o} quote={summary?.price ?? null} />
        </div>
      </div>
      <FairValueChip ticker={ticker} stored={summary?.fair_value ?? null} />
      {holding ? <PositionCard holding={holding} /> : null}
      <Actions ticker={ticker} name={name} />
    </div>
  );
}

export function CompanyShell({ ticker, children }: { ticker: string; children: ReactNode }) {
  const pathname = usePathname();
  const overviewFetcher = useCallback(
    () => getBrowserApiClient().GET("/companies/{ticker}/overview", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state: overview } = useApiResource(overviewFetcher);
  const { state: checks } = useCompanyChecks(ticker);
  // One small read for the price, day change, stored fair value and position, instead of the
  // whole holdings list.
  const summaryFetcher = useCallback(
    () => getBrowserApiClient().GET("/companies/{ticker}/summary", { params: { path: { ticker } } }),
    [ticker],
  );
  const { state: summaryState } = useApiResource(summaryFetcher);
  const [report, setReport] = useState<FairValueReport | null>(null);

  const o = overview.kind === "ok" ? overview.data : overview.kind === "error" ? overview.previous : null;
  const summary = summaryState.kind === "ok" ? summaryState.data : null;
  const name = displayName(o, ticker);
  // A fund has no earnings, segments or results dates: its own page reads its holdings,
  // factsheet and look-through instead of a company page full of "unknown".
  const router = useRouter();
  const fundIsin = summary?.fund_isin ?? null;
  useEffect(() => {
    if (fundIsin) router.replace(`/funds/${encodeURIComponent(fundIsin)}`);
  }, [fundIsin, router]);

  const slug = slugFromPath(pathname);
  const active = sectionForSlug(slug);

  // Nine items: cheap enough to build every render (the React Compiler memoises it anyway).
  const axes = checks.kind === "ok" ? checks.data.axes : [];
  const links: EntitySectionLink[] = COMPANY_SECTIONS.map((s) => {
    const a = s.axis ? axes.find((x) => x.axis === s.axis) : undefined;
    const score = num(a?.score);
    return {
      key: s.id,
      href: companyHref(ticker, s.routes[0].slug),
      label: s.label,

      badge: score !== null && a ? `${Math.round(score)}/${a.max}` : null,
      active: active?.id === s.id,
    };
  });

  const subNav = active
    ? active.routes.map((r) => ({ href: companyHref(ticker, r.slug), label: r.label, active: r.slug === slug }))
    : [];

  return (
    <OverviewProvider value={overview}>
      <ChecksProvider value={checks}>
        <FairValueProvider value={{ report, setReport }}>
          <EntityLayout
            navLabel="Company sections"
            sections={links}
            sidebar={<Sidebar o={o} ticker={ticker} summary={summary} />}
            header={<CompactHeader o={o} ticker={ticker} summary={summary} />}
          >
            {active ? <SectionHead section={active} ticker={ticker} name={name} /> : null}
            <SectionSubNav items={subNav} label={`${active?.label ?? "Section"} pages`} />
            {children}
            {active && isSectionHome(active, slug) ? <HoldingSectionPanels ticker={ticker} section={active.id} /> : null}
          </EntityLayout>
        </FairValueProvider>
      </ChecksProvider>
    </OverviewProvider>
  );
}
