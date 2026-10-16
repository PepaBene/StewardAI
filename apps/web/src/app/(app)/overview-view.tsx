"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { KpiStrip, type KpiTileProps } from "@/components/insight/kpi-tile";
import { ScorecardPanel } from "@/components/insight/scorecard-panel";
import { UpdatesFeedPanel } from "@/components/insight/updates-feed";
import { PageHeader } from "@/components/page-header";
import { AllocationPanel } from "@/components/portfolio/allocation-panel";
import type { AllocationItem } from "@/components/portfolio/allocation-bars";
import { ContributionCard } from "@/components/portfolio/contribution-card";
import { FreshnessLabel } from "@/components/portfolio/freshness-label";
import { OverviewHero, type HistoryLoad } from "@/components/portfolio/overview-hero";
import { RiskGlance } from "@/components/portfolio/risk-glance";
import { useSyncStatus } from "@/components/sync/sync-status-provider";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type PortfolioHistory, type PortfolioOverview } from "@/lib/api/client";
import { formatDate, formatMoney, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { money, pct } from "@/lib/insight/income";
import { num } from "@/lib/insight/insight";
import { formatCompactMoney, formatSignedPct } from "@/lib/portfolio/money";
import { DEFAULT_HERO_RANGE, HISTORY_DAYS, indexedRange, lastChange, periodReturn, rangeStart, type HeroRangeId } from "@/lib/portfolio/performance";
import { shortTicker } from "@/lib/portfolio/ticker";
import { benchmarkVerdict, incomeChangeVerdict, unrealisedVerdict } from "@/lib/verdicts/portfolio";
import { cn } from "@/lib/utils";

const fetchOverview = () => getBrowserApiClient().GET("/portfolio/overview");
const fetchHistory = () => getBrowserApiClient().GET("/portfolio/history", { params: { query: { days: HISTORY_DAYS } } });
const fetchDividends = () => getBrowserApiClient().GET("/portfolio/dividends");

export function OverviewView() {
  const { state, reload } = useApiResource(fetchOverview);

  if (state.kind === "loading") {
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
        <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
        Loading portfolio…
      </p>
    );
  }
  if (state.kind === "error" && state.previous === null) {
    return (
      <AlertMessage title={state.title}>
        <p>{state.detail}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => void reload()}>
          <RefreshCwIcon aria-hidden="true" />
          Try again
        </Button>
      </AlertMessage>
    );
  }
  const data = state.kind === "ok" ? state.data : state.previous!;
  return (
    <>
      {state.kind === "error" ? (
        <AlertMessage title={`Could not refresh: ${state.title}`} className="mb-6">
          <p>Showing the last data loaded. {state.detail}</p>
        </AlertMessage>
      ) : null}
      <Overview data={data} />
    </>
  );
}

function Header({ data }: { data: PortfolioOverview }) {
  return (
    <PageHeader
      kicker="Portfolio"
      title="Overview"
      description="What your portfolio is worth, how it is doing and what changed."
      aside={
        data.freshness ? (
          <>
            <FreshnessLabel syncedAt={data.freshness.synced_at} snapshotDate={data.freshness.snapshot_date} />
            {data.base ? <span>· {data.base.fx_source === "cnb" ? "ČNB" : "ECB"} rates · Trading212</span> : null}
          </>
        ) : null
      }
    />
  );
}

function SyncFailureNotice({ data }: { data: PortfolioOverview }) {
  const last = data.last_sync;
  if (!last || last.status !== "failed") return null;
  if (data.freshness && last.id < data.freshness.job_id) return null;
  return (
    <AlertMessage title="The latest sync failed">
      <p>
        {last.error ?? "No error message was recorded."}
        {data.freshness ? " The figures below are from the last successful sync." : null}
      </p>
    </AlertMessage>
  );
}

function historyLoad(state: ReturnType<typeof useApiResource<PortfolioHistory>>["state"]): HistoryLoad {
  if (state.kind === "loading") return { kind: "loading" };
  if (state.kind === "error") return state.previous ? { kind: "ok", data: state.previous } : { kind: "error", message: state.title };
  return { kind: "ok", data: state.data };
}

function Overview({ data }: { data: PortfolioOverview }) {
  const { startSync, starting, view } = useSyncStatus();
  const history = historyLoad(useApiResource(fetchHistory).state);
  const dividends = useApiResource(fetchDividends).state;
  const [range, setRange] = useState<HeroRangeId>(DEFAULT_HERO_RANGE);
  const h = history.kind === "ok" && !history.data.gap ? history.data : null;

  if (!data.freshness || !data.account || !data.base) {
    return (
      <div className="space-y-6">
        <Header data={data} />
        <SyncFailureNotice data={data} />
        <EmptyState title="No portfolio snapshot yet">
          <p>The first Trading212 sync creates one. It runs every evening on schedule, or start one now.</p>
          <Button className="mt-4" onClick={() => void startSync()} disabled={starting || view.kind === "active"}>
            <RefreshCwIcon aria-hidden="true" className={cn(starting && "animate-spin")} />
            Sync now
          </Button>
        </EmptyState>
      </div>
    );
  }

  const { account, base } = data;
  const baseCcy = base.base_currency;
  const pplPct = account.invested_cost ? account.unrealized_ppl / account.invested_cost : null;

  // Answer strip figures.
  const asOf = h?.points.at(-1)?.date ?? null;
  const change = h ? lastChange(h.points, h.values) : null;
  const ytdStart = asOf ? rangeStart("ytd", asOf) : null;
  const ytd = h && asOf ? periodReturn(h.points, ytdStart) : null;
  const ytdBench = h && asOf && h.benchmark.length ? indexedRange(h.points, h.benchmark, ytdStart).benchmarkReturn : null;
  const benchName = h?.benchmark_symbol ?? "the benchmark";
  const returnVerdict = benchmarkVerdict(ytd, ytdBench, benchName);

  const divData = dividends.kind === "ok" ? dividends.data : dividends.kind === "error" ? dividends.previous : null;
  const income = divData && divData.summary.next_12m !== null ? divData.summary : null;
  const incomeVerdict = incomeChangeVerdict(income ? num(income.change_vs_trailing) : null);
  const plVerdict = unrealisedVerdict(pplPct);
  const plSigned = account.unrealized_ppl >= 0 ? "gain" : "loss";

  const tiles: KpiTileProps[] = [
    {
      label: "Portfolio value",
      value: formatMoney(base.total, baseCcy, { decimals: 0 }),
      hero: false,
      change: change
        ? {
            text: `${change.amount !== null ? `${formatMoney(change.amount, baseCcy, { decimals: 0, signed: true })} ` : ""}(${formatSignedPct(change.pct, 2)})`,
            direction: change.pct > 0 ? "up" : change.pct < 0 ? "down" : "flat",
            period: `since ${formatDate(change.since)}`,
          }
        : undefined,
      note: `${formatPercent(base.total ? base.invested / base.total : null, { decimals: 0 })} invested · ${formatCompactMoney(base.cash, baseCcy)} cash`,
    },
    {
      label: asOf ? "Return this year" : "Return",
      value: ytd === null ? "—" : formatSignedPct(ytd),
      signed: ytd === null ? undefined : ytd >= 0 ? "gain" : "loss",
      note: ytdBench !== null ? `${benchName} ${formatSignedPct(ytdBench)}` : undefined,
      verdict: history.kind === "loading" ? undefined : { tone: returnVerdict.tone, text: returnVerdict.text },
    },
    {
      label: `Unrealised P/L${account.account_currency !== baseCcy ? ` (${account.account_currency})` : ""}`,
      value: formatCompactMoney(account.unrealized_ppl, account.account_currency, { signed: true }),
      signed: plSigned,
      note: pplPct === null ? undefined : `${formatSignedPct(pplPct)} on cost`,
      verdict: { tone: plVerdict.tone, text: plVerdict.text },
    },
    {
      label: "Income, next 12 months",
      value: income ? money(income.next_12m, income.currency, 0) : "—",
      note: income ? `${money(income.monthly_average, income.currency, 0)} a month · yield ${pct(income.yield_on_price, 1)}` : undefined,
      verdict: income
        ? { tone: incomeVerdict.tone, text: incomeVerdict.text }
        : dividends.kind === "loading"
          ? undefined
          : { tone: "unknown", text: "No dividend forecast yet." },
    },
  ];

  const allocationItems: AllocationItem[] = data.allocation.map((slice) => ({
    key: slice.key,
    label: slice.label === slice.key ? shortTicker(slice.key) : slice.label,
    sublabel: slice.key === "CASH" || slice.label === slice.key ? undefined : shortTicker(slice.key),
    value: slice.value_base,
    weight: slice.weight,
    muted: slice.key === "CASH",
  }));

  return (
    <div className="space-y-5">
      <Header data={data} />
      <SyncFailureNotice data={data} />
      {data.base_currency_mismatch ? (
        <AlertMessage tone="info" title="Base currency changed">
          <p>These figures are in {baseCcy}. The strategy now uses a different base currency; the next sync converts to it.</p>
        </AlertMessage>
      ) : null}

      <KpiStrip tiles={tiles} label="Portfolio at a glance" />

      <OverviewHero history={history} range={range} onRange={setRange} />

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
        <ScorecardPanel />
        <RiskGlance />
      </div>

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
        <ContributionCard />
        <AllocationPanel items={allocationItems} currency={baseCcy} fxSource={base.fx_source} />
      </div>

      <UpdatesFeedPanel title="What happened to your holdings" limit={8} />
      <p className="-mt-3 text-sm">
        <Link href="/news" className="font-medium text-accent-ink hover:underline">
          See every update on Markets › News
        </Link>
      </p>

      <FxFootnote data={data} />
    </div>
  );
}

function FxFootnote({ data }: { data: PortfolioOverview }) {
  const base = data.base!;
  const rates = data.fx_rates.filter((r) => r.rate_date !== null && r.currency !== base.base_currency);
  if (rates.length === 0) return null;
  return (
    <p className="text-xs text-text-muted">
      {base.fx_source === "cnb" ? "ČNB" : "ECB"} reference rates used:{" "}
      {rates.map((r, i) => (
        <span key={r.currency} className="num">
          {i > 0 ? "; " : null}1 {r.currency} = {r.rate.toFixed(4)} {base.base_currency} ({formatDate(r.rate_date!)})
        </span>
      ))}
      .
    </p>
  );
}
