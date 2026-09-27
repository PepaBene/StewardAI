"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { AllocationBars, type AllocationItem } from "@/components/portfolio/allocation-bars";
import { DriftBar } from "@/components/portfolio/drift-bar";
import { DriftStatusBadge, isOutOfBand } from "@/components/portfolio/drift-status-badge";
import { FreshnessLabel } from "@/components/portfolio/freshness-label";
import { PerformanceCard } from "@/components/portfolio/performance-card";
import { StatCard } from "@/components/portfolio/stat-card";
import { useSyncStatus } from "@/components/sync/sync-status-provider";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getBrowserApiClient, type PortfolioOverview } from "@/lib/api/client";
import { formatDate, formatMoney, formatPercent, formatPp, gainLossTone } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cn } from "@/lib/utils";

const fetchOverview = () => getBrowserApiClient().GET("/portfolio/overview");

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

function PageHeader({ data }: { data: PortfolioOverview }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-2">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <p className="mt-1 text-sm text-text-muted">
          Portfolio value, allocation and drift from target weights.
        </p>
      </div>
      {data.freshness ? (
        <FreshnessLabel
          syncedAt={data.freshness.synced_at}
          snapshotDate={data.freshness.snapshot_date}
        />
      ) : null}
    </div>
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

function Overview({ data }: { data: PortfolioOverview }) {
  const { startSync, starting, view } = useSyncStatus();

  if (!data.freshness || !data.account || !data.base) {
    return (
      <div className="space-y-6">
        <PageHeader data={data} />
        <SyncFailureNotice data={data} />
        <EmptyState title="No portfolio snapshot yet">
          <p>
            The first Trading212 sync creates one. It runs every evening on schedule, or start
            one now.
          </p>
          <Button
            className="mt-4"
            onClick={() => void startSync()}
            disabled={starting || view.kind === "active"}
          >
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

  return (
    <div className="space-y-6">
      <PageHeader data={data} />
      <SyncFailureNotice data={data} />
      {data.base_currency_mismatch ? (
        <AlertMessage tone="info" title="Base currency changed">
          <p>
            These figures are in {baseCcy}. The strategy now uses a different base currency; the
            next sync converts to it.
          </p>
        </AlertMessage>
      ) : null}

      <section aria-label="Totals" className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label={`Total value (${baseCcy})`}
          value={formatMoney(base.total, baseCcy)}
          sub={
            <>
              Trading212 reports {formatMoney(account.total_value, account.account_currency)}
            </>
          }
        />
        <StatCard
          label="Invested"
          value={formatMoney(base.invested, baseCcy)}
          sub={<>{formatPercent(base.total ? base.invested / base.total : null)} of total</>}
        />
        <StatCard
          label="Cash"
          value={formatMoney(base.cash, baseCcy)}
          sub={<>{formatPercent(base.total ? base.cash / base.total : null)} of total</>}
        />
        <StatCard
          label={`Unrealised P/L (${account.account_currency})`}
          value={formatMoney(account.unrealized_ppl, account.account_currency, { signed: true })}
          valueClassName={TONE_CLASS[pplTone]}
          trend={pplTone}
          sub={<>{formatPercent(pplPct, { signed: true })} on cost · Trading212 figure</>}
        />
      </section>

      <PerformanceCard />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Allocation</CardTitle>
            <CardDescription>
              By holding, in {baseCcy} at {base.fx_source === "cnb" ? "ČNB" : "ECB"} reference
              rates.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <AllocationBars items={allocationItems} currency={baseCcy} label="Allocation by holding" />
          </CardContent>
        </Card>
        <DriftCard data={data} />
      </div>

      {drift ? <CategoryCards data={data} /> : null}

      <FxFootnote data={data} />
    </div>
  );
}

function DriftCard({ data }: { data: PortfolioOverview }) {
  const drift = data.drift;
  const targeted = drift?.positions.filter((p) => p.status !== "no_target") ?? [];
  const untargeted = drift?.positions.filter((p) => p.status === "no_target") ?? [];
  const cash = drift?.cash;
  const cashRow = cash && cash.status !== "no_target" ? cash : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Drift</CardTitle>
        <CardDescription>
          {drift && drift.out_of_band > 0
            ? `${drift.out_of_band} outside their band.`
            : "Weight against each target and its band."}{" "}
          Weights are of {drift?.weight_basis === "invested" ? "invested value" : "total value incl. cash"}.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {data.strategy_config_version_id === null ? (
          <AlertMessage tone="info" title="No strategy saved yet">
            <p>
              Drift needs target weights from the strategy config.{" "}
              <Link href="/config/strategy" className="text-accent hover:underline">
                Set targets in the strategy editor
              </Link>
              .
            </p>
          </AlertMessage>
        ) : targeted.length === 0 && !cashRow ? (
          <p className="text-sm text-text-muted">The strategy sets no position or cash targets.</p>
        ) : (
          <div className="-mx-6 overflow-x-auto px-6">
            <table className="w-full text-sm">
              <caption className="sr-only">Drift from target weights</caption>
              <thead>
                <tr className="border-b border-border text-left text-text-muted">
                  <th scope="col" className="py-2 pr-3 font-medium">Holding</th>
                  <th scope="col" className="py-2 px-3 text-right font-medium">Weight</th>
                  <th scope="col" className="py-2 px-3 text-right font-medium">Target</th>
                  <th scope="col" className="py-2 px-3 text-right font-medium">Drift</th>
                  <th scope="col" className="py-2 pl-3 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {targeted.map((p) => (
                  <tr key={p.ticker} className="border-b border-border last:border-0">
                    <th scope="row" className="py-2 pr-3 text-left font-mono text-xs font-normal">
                      {p.ticker}
                      {p.held ? null : <span className="ml-1 font-sans text-text-muted">(not held)</span>}
                    </th>
                    <td className="num whitespace-nowrap py-2 px-3 text-right">{formatPercent(p.weight)}</td>
                    <td className="num whitespace-nowrap py-2 px-3 text-right text-text-muted">
                      {formatPercent(p.target_weight)}
                      <span className="block text-xs">
                        {formatPercent(p.lower)}–{formatPercent(p.upper)}
                      </span>
                    </td>
                    <td className={cn("num whitespace-nowrap py-2 px-3 text-right", isOutOfBand(p.status) && "font-semibold")}>
                      <span className="inline-flex items-center gap-2">
                        <DriftBar drift={p.drift} />
                        {formatPp(p.drift)}
                      </span>
                    </td>
                    <td className="py-2 pl-3">
                      <DriftStatusBadge status={p.status} />
                    </td>
                  </tr>
                ))}
                {cashRow ? (
                  <tr>
                    <th scope="row" className="py-2 pr-3 text-left font-normal">Cash</th>
                    <td className="num whitespace-nowrap py-2 px-3 text-right">{formatPercent(cashRow.weight)}</td>
                    <td className="num whitespace-nowrap py-2 px-3 text-right text-text-muted">
                      {formatPercent(cashRow.target_weight)}
                      <span className="block text-xs">
                        {formatPercent(cashRow.lower)}–{formatPercent(cashRow.upper)}
                      </span>
                    </td>
                    <td className="num whitespace-nowrap py-2 px-3 text-right">
                      <span className="inline-flex items-center gap-2">
                        <DriftBar drift={cashRow.drift} />
                        {formatPp(cashRow.drift)}
                      </span>
                    </td>
                    <td className="py-2 pl-3">
                      <DriftStatusBadge status={cashRow.status} />
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        )}
        {untargeted.length > 0 && data.strategy_config_version_id !== null ? (
          <p className="mt-3 text-xs text-text-muted">
            No target set for {untargeted.map((p) => p.ticker).join(", ")}.{" "}
            <Link href="/holdings" className="text-accent underline-offset-4 hover:underline">
              See all holdings
            </Link>
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function CategoryCards({ data }: { data: PortfolioOverview }) {
  const baseCcy = data.base!.base_currency;
  const dimensions = (data.drift?.categories ?? []).filter((c) =>
    c.buckets.some((b) => b.bucket !== "Unclassified" || b.target_weight !== null),
  );
  if (dimensions.length === 0) return null;
  return (
    <section aria-label="Allocation by category" className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
      {dimensions.map((dimension) => (
        <Card key={dimension.dimension}>
          <CardHeader>
            <CardTitle className="capitalize">{dimension.dimension.replaceAll("_", " ")}</CardTitle>
            {dimension.dimension === "currency" ? (
              <CardDescription>From each instrument&apos;s trading currency.</CardDescription>
            ) : null}
          </CardHeader>
          <CardContent className="space-y-3">
            <AllocationBars
              currency={baseCcy}
              label={`Allocation by ${dimension.dimension}`}
              items={dimension.buckets.map((b) => ({
                key: b.bucket,
                label: b.bucket,
                value: b.value_base,
                weight: b.weight,
                muted: b.bucket === "Unclassified",
              }))}
            />
            {dimension.buckets.some((b) => b.status !== "no_target") ? (
              <ul className="space-y-1 text-xs">
                {dimension.buckets
                  .filter((b) => b.status !== "no_target")
                  .map((b) => (
                    <li key={b.bucket} className="flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-2">
                        <DriftBar drift={b.drift} scale={0.05} />
                        {b.bucket}: target {formatPercent(b.target_weight)}, drift{" "}
                        <span className="num">{formatPp(b.drift)}</span>
                      </span>
                      <DriftStatusBadge status={b.status} />
                    </li>
                  ))}
              </ul>
            ) : null}
          </CardContent>
        </Card>
      ))}
    </section>
  );
}

function FxFootnote({ data }: { data: PortfolioOverview }) {
  const base = data.base!;
  const rates = data.fx_rates.filter((r) => r.rate_date !== null);
  if (rates.length === 0) return null;
  return (
    <p className="text-xs text-text-muted">
      {base.fx_source === "cnb" ? "ČNB" : "ECB"} reference rates used:{" "}
      {rates.map((r, i) => (
        <span key={r.currency} className="num">
          {i > 0 ? "; " : null}1 {r.currency} = {r.rate.toFixed(4)} {base.base_currency} (
          {formatDate(r.rate_date!)})
        </span>
      ))}
      .
    </p>
  );
}
