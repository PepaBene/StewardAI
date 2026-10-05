"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import type { AllocationItem } from "@/components/portfolio/allocation-bars";
import { AllocationPanel } from "@/components/portfolio/allocation-panel";
import { CashFlowPanel } from "@/components/portfolio/cash-flow-panel";
import { ConcentrationCompliance } from "@/components/portfolio/concentration-compliance";
import { ContributionCard } from "@/components/portfolio/contribution-card";
import { DriftPanel, DriftTable } from "@/components/portfolio/drift-panel";
import { PortfolioDataContextSection } from "@/components/portfolio/data-context-section";
import { ExposurePanel } from "@/components/portfolio/exposure-panel";
import { useLookThrough } from "@/components/portfolio/look-through-exposure";
import { FreshnessLabel } from "@/components/portfolio/freshness-label";
import { OverviewHero, type HistoryLoad } from "@/components/portfolio/overview-hero";
import { ReturnsHeatmap } from "@/components/portfolio/returns-heatmap";
import { RiskPanel } from "@/components/portfolio/risk-panel";
import { RiskStatTile } from "@/components/portfolio/risk-stat-tile";
import { StatTile } from "@/components/portfolio/stat-tile";
import { fetchValuation, ValuationBenchmarkPanel } from "@/components/portfolio/valuation-benchmark-panel";
import { useSyncStatus } from "@/components/sync/sync-status-provider";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type PortfolioHistory, type PortfolioOverview } from "@/lib/api/client";
import { formatDate, formatMoney, formatPercent, gainLossTone } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { VALUATION_SCALE, valuationAxis } from "@/lib/portfolio/valuation";
import { herfindahlIndex } from "@/lib/portfolio/concentration";
import { lookThroughRisk } from "@/lib/portfolio/look-through";
import { formatCompactMoney, formatSignedPct } from "@/lib/portfolio/money";
import { DEFAULT_HERO_RANGE, HISTORY_DAYS, rangeStart, sliceFrom, trailingRisk, type HeroRangeId } from "@/lib/portfolio/performance";
import { foreignCurrencyShare, riskAxes } from "@/lib/portfolio/risk-profile";
import { cn } from "@/lib/utils";

const fetchOverview = () => getBrowserApiClient().GET("/portfolio/overview");
const fetchHistory = () => getBrowserApiClient().GET("/portfolio/history", { params: { query: { days: HISTORY_DAYS } } });

const TONE_CLASS = { gain: "text-gain", loss: "text-loss", flat: "" };

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
      description="Portfolio value, allocation and drift from target weights."
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
  const [range, setRange] = useState<HeroRangeId>(DEFAULT_HERO_RANGE);
  const h = history.kind === "ok" && !history.data.gap ? history.data : null;
  const risk = useMemo(() => (h ? trailingRisk(h.points, h.benchmark) : null), [h]);
  const lookThrough = useLookThrough().report;
  const valuation = useApiResource(fetchValuation).state;
  const valuationData = valuation.kind === "ok" ? valuation.data : valuation.kind === "error" ? valuation.previous : null;
  const valuationRadar = valuationAxis(valuationData);

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

  const { account, base, drift } = data;
  const baseCcy = base.base_currency;
  const pplTone = gainLossTone(account.unrealized_ppl);
  const pplPct = account.invested_cost ? account.unrealized_ppl / account.invested_cost : null;

  const allocationItems: AllocationItem[] = data.allocation.map((slice) => ({
    key: slice.key,
    label: slice.label,
    sublabel: slice.key === "CASH" ? undefined : slice.key,
    value: slice.value_base,
    weight: slice.weight,
    muted: slice.key === "CASH",
  }));
  const positionValues = allocationItems.filter((i) => !i.muted).map((i) => i.value);
  const invested = positionValues.reduce((s, v) => s + v, 0);
  const hhi = invested > 0 ? herfindahlIndex(positionValues.map((v) => v / invested)) : null;
  const fxShare = foreignCurrencyShare(drift?.categories.find((c) => c.dimension === "currency")?.buckets, baseCcy, base.total);
  const axes = riskAxes({
    volatility: risk?.volatility ?? null,
    beta: risk?.beta ?? null,
    maxDrawdown: risk?.maxDrawdown ?? null,
    hhi,
    positions: positionValues.length,
    fxShare,
    benchmarkVolatility: risk?.benchmarkVolatility,
    benchmarkMaxDrawdown: risk?.benchmarkMaxDrawdown,
    lookThrough: lookThroughRisk(lookThrough),
    valuation: { portfolio: valuationRadar.portfolio, benchmark: valuationRadar.benchmark, rim: `P/E ${VALUATION_SCALE.pe}` },
  });

  // Tile sparklines follow the hero's range (absolute values in the history's currency).
  const asOf = h?.points.at(-1)?.date ?? null;
  const start = asOf ? rangeStart(range, asOf) : null;
  const sameCcy = h?.values_currency === baseCcy;
  const values = h && sameCcy ? sliceFrom(h.values, start) : [];
  const indexSpark = h ? sliceFrom(h.points, start).map((p) => p.close) : [];
  const rangeLabel = range === "max" ? "all history" : range.toUpperCase();
  const cash = drift?.cash;
  const today = data.freshness.snapshot_date;

  return (
    <div className="space-y-5">
      <Header data={data} />
      <SyncFailureNotice data={data} />
      {data.base_currency_mismatch ? (
        <AlertMessage tone="info" title="Base currency changed">
          <p>These figures are in {baseCcy}. The strategy now uses a different base currency; the next sync converts to it.</p>
        </AlertMessage>
      ) : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <OverviewHero
          total={base.total}
          currency={baseCcy}
          subline={
            account.account_currency !== baseCcy || Math.abs(account.total_value - base.total) > 0.5
              ? <>Trading212 reports {formatMoney(account.total_value, account.account_currency)}</>
              : <>Trading212 reports the same total in {account.account_currency}</>
          }
          history={history}
          range={range}
          onRange={setRange}
        />
        <section aria-label="Totals" className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <StatTile
            label="Invested"
            value={formatCompactMoney(base.invested, baseCcy)}
            sub={<>{formatPercent(base.total ? base.invested / base.total : null)} of total</>}
            spark={values.map((v) => v.invested)}
            sparkNote={`Invested value, ${rangeLabel}`}
          />
          <StatTile
            label="Cash"
            value={formatCompactMoney(base.cash, baseCcy)}
            sub={
              <>
                {formatPercent(base.total ? base.cash / base.total : null)}
                {cash && cash.target_weight !== null ? <> · target {formatPercent(cash.target_weight)}</> : " of total"}
              </>
            }
            spark={values.map((v) => v.cash)}
            sparkNote={`Cash, ${rangeLabel}`}
          />
          <StatTile
            label={`Unrealised P/L${account.account_currency !== baseCcy ? ` (${account.account_currency})` : ""}`}
            value={<span className={TONE_CLASS[pplTone]}>{formatCompactMoney(account.unrealized_ppl, account.account_currency, { signed: true })}</span>}
            sub={
              <>
                <span aria-hidden="true">{pplTone === "gain" ? "▲ " : pplTone === "loss" ? "▼ " : ""}</span>
                {formatSignedPct(pplPct)} on cost · Trading212 figure
              </>
            }
            subClassName={TONE_CLASS[pplTone]}
            spark={values.map((v) => v.unrealised)}
            sparkNote={`Unrealised P/L, ${rangeLabel}`}
          />
          <RiskStatTile
            figures={risk}
            historyLoading={history.kind === "loading"}
            spark={indexSpark}
            sparkNote={`Flow-adjusted performance index, ${rangeLabel}`}
          />
        </section>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2 xl:grid-cols-3">
        <AllocationPanel items={allocationItems} currency={baseCcy} fxSource={base.fx_source} />
        <DriftPanel data={data} />
        <RiskPanel
          figures={risk}
          axes={axes}
          fxShare={fxShare}
          benchmarkSymbol={h?.benchmark_symbol ?? null}
          loading={history.kind === "loading"}
          lookThrough={lookThrough}
        />
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <ReturnsHeatmap points={h?.points ?? []} loading={history.kind === "loading"} />
        <ConcentrationCompliance positions={positionValues} drift={drift} hasStrategy={data.strategy_config_version_id !== null} />
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]">
        <CashFlowPanel year={Number(today.slice(0, 4))} today={today} />
        {drift ? <ExposurePanel drift={drift} currency={baseCcy} lookThrough={lookThrough} /> : null}
      </div>

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
        <ContributionCard />
        <DriftTable data={data} />
      </div>

      <PortfolioDataContextSection />

      <ValuationBenchmarkPanel state={valuation} />

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
