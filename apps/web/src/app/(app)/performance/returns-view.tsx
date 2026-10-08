"use client";

import { CircleCheckIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate } from "@/components/company/primitives";
import { DataAgeBadge } from "@/components/freshness/data-age-badge";
import { LineChart } from "@/components/performance/line-chart";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { figureReason, ledgerSentence, windowName, type PerformanceData, type WindowData } from "@/lib/performance/performance";

import { SavedReports } from "./saved-reports";
import { Formula, Money, PlainReturn, Points, ReturnCell, TD, TH, TH_LEFT } from "./shared";

const fetchPerformance = () => getBrowserApiClient().GET("/portfolio/performance");

/** How old the prices, exchange rates and benchmark under a chart are. */
function FreshnessBadge({ data }: { data: PerformanceData }) {
  const f = data.freshness;
  if (!f) return null;
  return (
    <DataAgeBadge
      staleAfterDays={f.stale_after_days}
      items={[
        { label: "Prices and rates", date: f.prices_as_of, ageDays: f.prices_age_days },
        ...(data.benchmark_label ? [{ label: "Benchmark", date: f.benchmark_as_of, ageDays: f.benchmark_age_days }] : []),
      ]}
    />
  );
}

function LedgerStrip({ data }: { data: PerformanceData }) {
  const sentence = ledgerSentence(data.ledger);
  if (data.ledger.status === "clean") {
    return (
      <p className="flex items-center gap-2 text-sm text-text-muted">
        <CircleCheckIcon aria-hidden="true" className="size-4 text-verified" />
        <span>
          <span className="font-medium text-text">Ledger checks out.</span> {sentence}
        </span>
      </p>
    );
  }
  return (
    <AlertMessage title="Read these figures with the ledger gaps in mind">
      {sentence}{" "}
      <Link href="/performance/ledger" className="text-accent-ink underline underline-offset-2">
        See what is missing
      </Link>
    </AlertMessage>
  );
}

function BenchmarkCell({ w }: { w: WindowData }) {
  if (!w.benchmark) return <Absent reason="missing: no benchmark chosen" />;
  const r = w.benchmark.benchmark_return;
  return r === null || r === undefined ? <Absent reason={`missing: ${figureReason(w.benchmark.active_return) ?? "no closes stored"}`} /> : <PlainReturn value={r} />;
}

function ActiveCell({ w }: { w: WindowData }) {
  const active = w.benchmark?.active_return;
  if (!active) return <span className="text-text-muted">—</span>;
  if (active.value === null || active.value === undefined) {
    return <Absent reason={`missing: ${figureReason(active) ?? "not computable"}`} />;
  }
  return (
    <span title={(active.warnings ?? []).join(" ") || undefined}>
      <Points value={active.value} />
      {(active.warnings ?? []).length > 0 ? <span className="ml-1 text-[11px] text-text-muted">qualified</span> : null}
    </span>
  );
}

function StatementTable({ data }: { data: PerformanceData }) {
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto border border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">Portfolio return by window</caption>
          <thead>
            <tr className="bg-surface-muted">
              <th scope="col" className={TH_LEFT}>Window</th>
              <th scope="col" className={TH}>Your investments</th>
              <th scope="col" className={TH}>A year</th>
              <th scope="col" className={TH}>Your money</th>
              <th scope="col" className={TH}>{data.benchmark_label ? "Benchmark" : "No benchmark"}</th>
              <th scope="col" className={TH}>Ahead or behind</th>
              <th scope="col" className={TH}>Gain</th>
            </tr>
          </thead>
          <tbody>
            {data.windows.map((w) => (
              <tr key={w.label} className="border-t border-border-soft align-top">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  {windowName(w.label)}
                  <span className="block text-[11px] text-text-muted">
                    {w.start && w.end ? `${formatDate(w.start)} to ${formatDate(w.end)}` : (w.reason ?? "no data")}
                  </span>
                </th>
                <td className={TD}><ReturnCell figure={w.twr} /></td>
                <td className={TD}>{w.twr_annualized !== null && w.twr_annualized !== undefined ? <PlainReturn value={w.twr_annualized} /> : <span className="text-text-muted">under a year</span>}</td>
                <td className={TD}><ReturnCell figure={w.xirr} /></td>
                <td className={TD}><BenchmarkCell w={w} /></td>
                <td className={TD}><ActiveCell w={w} /></td>
                <td className={TD}><Money value={w.gain} currency={data.base_currency} signed /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="max-w-3xl text-xs text-text-muted">
        <span className="font-medium text-text">Your investments</span> removes your deposits and withdrawals, so it shows how the holdings did.{" "}
        <span className="font-medium text-text">Your money</span> is the annual rate on what you actually paid in and when, so it depends on your timing.
        Gain is in {data.base_currency}, after flows.
      </p>
    </div>
  );
}

function Costs({ data }: { data: PerformanceData }) {
  const c = data.costs;
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker="Last 12 months" title="What trading cost you" />
      <dl className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div><dt className="text-xs text-text-muted">Fees on fills</dt><dd className="num text-lg font-semibold"><Money value={c.fill_fees} currency={c.currency} decimals={2} /></dd></div>
        <div><dt className="text-xs text-text-muted">Account fees</dt><dd className="num text-lg font-semibold"><Money value={c.cash_fees} currency={c.currency} decimals={2} /></dd></div>
        <div><dt className="text-xs text-text-muted">Total</dt><dd className="num text-lg font-semibold"><Money value={c.total} currency={c.currency} decimals={2} /></dd></div>
        <div>
          <dt className="text-xs text-text-muted">Share of average value</dt>
          <dd className="num text-lg font-semibold">{c.fraction_of_average_value === null || c.fraction_of_average_value === undefined ? <Absent reason="missing: no average value" /> : formatPercent(c.fraction_of_average_value, { decimals: 2 })}</dd>
        </div>
      </dl>
      <p className="mt-2 text-xs text-text-muted">{c.note}</p>
    </Blueprint>
  );
}

function Timeline({ data }: { data: PerformanceData }) {
  if (data.timeline.length === 0) return null;
  const rows = [...data.timeline].reverse();
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker={`Account currency, ${data.account_currency ?? "unknown"}`} title="Money in, money out, dividends and fees by month" />
      <div className="mt-3 max-h-80 overflow-auto border border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">Monthly deposits, withdrawals, dividends and fees</caption>
          <thead>
            <tr className="sticky top-0 bg-surface-muted">
              <th scope="col" className={TH_LEFT}>Month</th>
              <th scope="col" className={TH}>Deposits</th>
              <th scope="col" className={TH}>Withdrawals</th>
              <th scope="col" className={TH}>Dividends</th>
              <th scope="col" className={TH}>Fees</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.month} className="border-t border-border-soft">
                <th scope="row" className="px-3 py-1 text-left font-normal">{m.month}</th>
                <td className={TD}>{m.deposits ? <Money value={m.deposits} currency={data.account_currency} /> : "—"}</td>
                <td className={TD}>{m.withdrawals ? <Money value={m.withdrawals} currency={data.account_currency} /> : "—"}</td>
                <td className={TD}>{m.dividends ? <Money value={m.dividends} currency={data.account_currency} decimals={2} /> : "—"}</td>
                <td className={TD}>{m.fees ? <Money value={m.fees} currency={data.account_currency} decimals={2} /> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Blueprint>
  );
}

export function ReturnsView() {
  const { state } = useApiResource(fetchPerformance);
  const [shown, setShown] = useState<{ data: PerformanceData; label: string } | null>(null);
  return (
    <div className="flex flex-col gap-5">
      {shown ? (
        <AlertMessage tone="info" title={`Showing the report you saved as "${shown.label}"`}>
          These are the figures as they stood when it was saved, not today&apos;s.{" "}
          <button type="button" onClick={() => setShown(null)} className="text-accent-ink underline underline-offset-2">
            Back to today
          </button>
        </AlertMessage>
      ) : null}
      {shown ? (
        <ReportBody data={shown.data} />
      ) : (
        <ResourceGate state={state} label="performance">
          {(data: PerformanceData) => <ReportBody data={data} />}
        </ResourceGate>
      )}
      <SavedReports onOpen={(data, label) => setShown({ data, label })} />
    </div>
  );
}

function ReportBody({ data }: { data: PerformanceData }) {
  return (
    <>
      {
        data.status === "unavailable" && data.windows.length === 0 ? (
          <AlertMessage tone="info" title="Not enough history yet">
            {data.gap ?? "Returns need at least two daily snapshots."}
          </AlertMessage>
        ) : (
          <div className="flex flex-col gap-5">
            <LedgerStrip data={data} />
            <Blueprint className="px-[22px] py-4">
              <PanelHeading
                kicker={`Base currency ${data.base_currency ?? "unknown"} · as of ${data.as_of ? formatDate(data.as_of) : "unknown"}`}
                title="Returns"
              />
              <div className="mt-3">
                <StatementTable data={data} />
              </div>
            </Blueprint>
            <Blueprint className="px-[22px] py-4">
              <PanelHeading kicker="Deposits and withdrawals removed" title="Growth of 100" />
              <div className="mt-3">
                <LineChart
                  label="Portfolio value indexed to 100 at the first snapshot, with the benchmark rebased to the same day"
                  series={[
                    { name: "Your investments", points: data.index, tone: "primary" },
                    ...(data.benchmark_label ? [{ name: data.benchmark_label, points: data.benchmark_index, tone: "benchmark" as const }] : []),
                  ]}
                  baseline={100}
                  format={(n) => n.toFixed(0)}
                />
              </div>
              <FreshnessBadge data={data} />
              {data.freshness_note ? <p className="mt-2 text-xs text-text-muted">{data.freshness_note}</p> : null}
            </Blueprint>
            <Blueprint className="px-[22px] py-4">
              <PanelHeading kicker="Rolling 90 days" title="How the last quarter has looked at each point" />
              <div className="mt-3">
                <LineChart
                  label="Rolling 90-day return of the portfolio, deposits and withdrawals removed"
                  series={[{ name: "90-day return", points: data.rolling_90d, tone: "primary" }]}
                  baseline={0}
                  format={(n) => formatPercent(n, { decimals: 0 })}
                />
              </div>
            </Blueprint>
            <div className="grid gap-5 lg:grid-cols-2">
              <Costs data={data} />
              <Blueprint className="px-[22px] py-4">
                <PanelHeading kicker="Data used" title="Dates and freshness" />
                <dl className="mt-3 space-y-1 text-sm">
                  <div className="flex justify-between gap-3"><dt className="text-text-muted">Latest snapshot</dt><dd className="num">{data.as_of ? formatDate(data.as_of) : "—"}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-text-muted">First ledger entry</dt><dd className="num">{data.ledger.first ? formatDate(data.ledger.first) : "—"}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-text-muted">Last ledger entry</dt><dd className="num">{data.ledger.last ? formatDate(data.ledger.last) : "—"}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-text-muted">Ledger entries</dt><dd className="num">{data.ledger.entries}</dd></div>
                </dl>
              </Blueprint>
            </div>
            <Timeline data={data} />
            <Formula lines={data.explain.formula} version={data.explain.formula_version} />
          </div>
        )
      }
    </>
  );
}
