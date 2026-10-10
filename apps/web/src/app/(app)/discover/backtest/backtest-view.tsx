"use client";

import { LoaderCircleIcon, PlayIcon } from "lucide-react";
import Link from "next/link";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { LineChart, type LinePoint } from "@/components/charts";
import { PageHeader } from "@/components/page-header";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { toNumber } from "@/lib/company/company";
import { buildFilters, type BacktestPeriod, type BacktestResponse, type CatalogueResponse, type FilterDraft } from "@/lib/discover/discover";
import { formatDate } from "@/lib/format";
import { pct, signedPct } from "@/lib/fundresearch/fund-research";
import { cn } from "@/lib/utils";

import { FilterBuilder, SELECT } from "../filter-builder";

type Run = { kind: "idle" } | { kind: "running" } | { kind: "error"; detail: string } | { kind: "ok"; result: BacktestResponse };

const FREQUENCIES = [
  ["annual", "Every year"],
  ["semiannual", "Every six months"],
  ["quarterly", "Every quarter"],
] as const;

function isoYearsAgo(years: number): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

function detailOf(error: unknown): string {
  if (error && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    return Array.isArray(d) ? d.map((x) => (typeof x === "string" ? x : JSON.stringify(x))).join(" ") : String(d);
  }
  return "";
}

/** Growth of 100 at the first rebalance, one point per rebalance date and one at the end.
 * A period with no return is skipped, as the backend skips it in the cumulative figures. */
function growth(periods: BacktestPeriod[], pick: (p: BacktestPeriod) => string | number | null | undefined): LinePoint[] {
  if (periods.length === 0) return [];
  let level = 100;
  const points: LinePoint[] = [{ date: periods[0].start, value: level }];
  for (const p of periods) {
    const r = toNumber(pick(p));
    if (r === null) continue;
    level *= 1 + r;
    points.push({ date: p.end, value: Math.round(level * 100) / 100 });
  }
  return points;
}

export function BacktestView() {
  const nextId = useRef(1);
  const [catalogue, setCatalogue] = useState<CatalogueResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<FilterDraft[]>([]);
  const [start, setStart] = useState(isoYearsAgo(5));
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [frequency, setFrequency] = useState<"annual" | "semiannual" | "quarterly">("annual");
  const [maxNames, setMaxNames] = useState("10");
  const [costBps, setCostBps] = useState("10");
  const [benchmark, setBenchmark] = useState("");
  const [sortBy, setSortBy] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [run, setRun] = useState<Run>({ kind: "idle" });
  const metrics = useMemo(() => catalogue?.metrics ?? [], [catalogue]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const r = await getBrowserApiClient().GET("/discover/catalogue", { params: { query: { kind: "stock" } } });
        if (cancelled) return;
        if (r.response.ok && r.data) setCatalogue(r.data);
        else setLoadError(`The metric catalogue failed to load (HTTP ${r.response.status}).`);
      } catch (err) {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const execute = async () => {
    const built = buildFilters(drafts, metrics);
    const problems = [...built.errors];
    const names = Number(maxNames);
    const bps = Number(costBps);
    if (!Number.isInteger(names) || names < 1 || names > 50) problems.push("Names held: enter a whole number from 1 to 50.");
    if (!Number.isInteger(bps) || bps < 0 || bps > 500) problems.push("Trading cost: enter whole basis points from 0 to 500.");
    if (!start || !end || end <= start) problems.push("The end date must be after the start date.");
    setErrors(problems);
    if (problems.length > 0) return;
    setRun({ kind: "running" });
    try {
      const r = await getBrowserApiClient().POST("/discover/backtest", {
        body: {
          filters: built.filters,
          missing: "exclude",
          start,
          end,
          frequency,
          max_names: names,
          cost_bps: bps,
          sort_by: sortBy || null,
          benchmark_ticker: benchmark.trim() ? benchmark.trim().toUpperCase() : null,
        },
      });
      if (r.response.ok && r.data) setRun({ kind: "ok", result: r.data });
      else setRun({ kind: "error", detail: detailOf(r.error) || `The test failed (HTTP ${r.response.status}).` });
    } catch (err) {
      setRun({ kind: "error", detail: err instanceof Error ? err.message : String(err) });
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research & Verification"
        title="Test a screen on the past"
        description="Replays a set of conditions on each past rebalance date, using only the companies listed then (delisted ones included) and only facts known by then, and charges an explicit trading cost. It describes what the rule would have done with the stored data. It is not a forecast."
        aside={<Link href="/discover" className="text-accent-ink hover:underline">Back to Discover</Link>}
      />

      <Blueprint aria-labelledby="bt-rule" className="space-y-4 px-[22px] py-5">
        <PanelHeading kicker="The same conditions as a screen" title={<span id="bt-rule">Rule</span>} />
        {loadError ? <AlertMessage title="Could not load the metrics">{loadError}</AlertMessage> : null}
        {catalogue ? (
          <FilterBuilder catalogue={metrics} drafts={drafts} onChange={setDrafts} nextId={() => nextId.current++} />
        ) : loadError ? null : (
          <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
            <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
            Loading the metric catalogue…
          </p>
        )}
        <div className="flex flex-wrap items-end gap-4">
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Rank the matches by</span>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} className={cn(SELECT, "w-56")}>
              <option value="">First condition</option>
              {metrics
                .filter((m) => m.enabled && m.kind === "numeric")
                .map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Names held</span>
            <Input inputMode="numeric" value={maxNames} onChange={(e) => setMaxNames(e.target.value)} className="w-20 text-right" />
          </label>
        </div>
      </Blueprint>

      <Blueprint aria-labelledby="bt-window" className="space-y-4 px-[22px] py-5">
        <PanelHeading kicker="What is assumed" title={<span id="bt-window">Window and costs</span>} />
        <div className="flex flex-wrap items-end gap-4">
          <label className="space-y-1 text-sm">
            <span className="block font-medium">First rebalance</span>
            <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="w-44" />
          </label>
          <label className="space-y-1 text-sm">
            <span className="block font-medium">End</span>
            <Input type="date" value={end} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setEnd(e.target.value)} className="w-44" />
          </label>
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Rebalance</span>
            <select value={frequency} onChange={(e) => setFrequency(e.target.value as typeof frequency)} className={cn(SELECT, "w-48")}>
              {FREQUENCIES.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Trading cost, basis points</span>
            <Input inputMode="numeric" value={costBps} onChange={(e) => setCostBps(e.target.value)} className="w-24 text-right" />
          </label>
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Compare with (ticker, optional)</span>
            <Input value={benchmark} onChange={(e) => setBenchmark(e.target.value)} placeholder="SPY" className="w-36" />
          </label>
        </div>
        <p className="max-w-[72ch] text-xs text-text-muted">
          The cost is charged on the share of the portfolio traded at each rebalance, so the first purchase counts in full. Zero means no cost is assumed and the result says so.
        </p>
        {errors.length > 0 ? (
          <AlertMessage title="Fix these to run the test">
            <ul className="list-disc pl-5">
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </AlertMessage>
        ) : null}
        <Button type="button" onClick={() => void execute()} disabled={run.kind === "running" || !catalogue}>
          {run.kind === "running" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : <PlayIcon aria-hidden="true" />}
          Run the test
        </Button>
      </Blueprint>

      {run.kind === "running" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Screening each rebalance date. This can take a minute.
        </p>
      ) : null}
      {run.kind === "error" ? <AlertMessage title="The test did not run">{run.detail}</AlertMessage> : null}
      {run.kind === "ok" ? <Result r={run.result} /> : null}
    </div>
  );
}

function Figure({ label, value, hint }: { label: string; value: string | null; hint?: string }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="num text-xl font-semibold">{value ?? <span className="text-base font-normal text-text-muted">not available</span>}</dd>
      {hint ? <dd className="max-w-[28ch] text-[11px] text-text-faint">{hint}</dd> : null}
    </div>
  );
}

function Result({ r }: { r: BacktestResponse }) {
  const [open, setOpen] = useState<string | null>(null);
  const net = growth(r.periods, (p) => p.net_return);
  const benchPeriods = r.periods.some((p) => p.benchmark_return !== null && p.benchmark_return !== undefined);
  const bench = benchPeriods ? growth(r.periods, (p) => p.benchmark_return) : [];
  return (
    <>
      <Blueprint aria-labelledby="bt-out" className="space-y-4 px-[22px] py-5">
        <PanelHeading
          kicker={`${formatDate(r.start)} to ${formatDate(r.end)}, ${r.periods.length} rebalances, ${r.cost_bps} bp cost`}
          title={<span id="bt-out">What the rule would have done</span>}
        />
        <dl className="grid grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
          <Figure label="After costs" value={signedPct(r.cumulative_net)} />
          <Figure label="Before costs" value={signedPct(r.cumulative_gross)} />
          <Figure label="Cost drag" value={pct(r.cost_drag)} hint="Gross minus net over the whole window." />
          <Figure label="Per year, after costs" value={signedPct(r.annualised_net)} hint="Only published for a window of a year or more." />
          {r.benchmark_ticker ? <Figure label={`${r.benchmark_ticker}, price`} value={signedPct(r.benchmark_cumulative)} /> : null}
        </dl>
        {net.length > 1 ? (
          <LineChart
            data={net}
            benchmark={bench.length > 1 ? bench : undefined}
            label="Rule, after costs"
            benchmarkLabel={r.benchmark_ticker ?? undefined}
            ariaLabel="Growth of 100 invested at the first rebalance, after costs"
            area={false}
          />
        ) : (
          <p className="text-sm text-text-muted">No period held anything, so there is no line to draw.</p>
        )}
        {r.warnings.length > 0 ? (
          <ul className="space-y-0.5 text-xs text-unconfirmed" role="note">
            {r.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : null}
      </Blueprint>

      <Blueprint aria-labelledby="bt-periods" className="space-y-3 px-[22px] py-5">
        <PanelHeading kicker="One row per rebalance" title={<span id="bt-periods">Period by period</span>} />
        <div className="overflow-x-auto border border-border">
          <table className="w-full min-w-[44rem] text-sm">
            <thead>
              <tr className="bg-surface-muted text-right text-xs text-text-muted">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">Period</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Members</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Matched</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Held</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Traded</th>
                <th scope="col" className="px-2 py-1.5 font-medium">Before costs</th>
                <th scope="col" className="px-2 py-1.5 font-medium">After costs</th>
                <th scope="col" className="px-2 py-1.5"><span className="sr-only">Names</span></th>
              </tr>
            </thead>
            <tbody>
              {r.periods.map((p) => {
                const key = p.start;
                const isOpen = open === key;
                return (
                  <Fragment key={key}>
                    <tr className="border-t border-border-soft text-right">
                      <th scope="row" className="px-3 py-1 text-left font-normal">
                        {formatDate(p.start)} to {formatDate(p.end)}
                      </th>
                      <td className="num px-2 py-1">{p.universe_size}</td>
                      <td className="num px-2 py-1">{p.matched}</td>
                      <td className="num px-2 py-1">{p.held.length}</td>
                      <td className="num px-2 py-1">{pct(p.turnover, 0)}</td>
                      <td className="num px-2 py-1">{signedPct(p.gross_return) ?? "n/m"}</td>
                      <td className="num px-2 py-1">{signedPct(p.net_return) ?? "n/m"}</td>
                      <td className="px-2 py-1">
                        <Button type="button" variant="ghost" size="sm" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : key)}>
                          Names
                        </Button>
                      </td>
                    </tr>
                    {isOpen ? (
                      <tr className="bg-surface-muted/50">
                        <td colSpan={8} className="space-y-1 px-4 py-3 text-xs">
                          {p.note ? <p className="text-unconfirmed">{p.note}</p> : null}
                          {p.held.length > 0 ? (
                            <ul className="grid gap-x-8 gap-y-0.5 sm:grid-cols-2">
                              {p.held.map((h) => (
                                <li key={h.ticker} className="flex justify-between gap-3">
                                  <span>
                                    {h.ticker}
                                    {h.ended_early ? <span className="text-unconfirmed"> (no price by period end, held at last price)</span> : null}
                                  </span>
                                  <span className="num">{signedPct(h.period_return)}</span>
                                </li>
                              ))}
                            </ul>
                          ) : null}
                          {p.dropped_no_price.length > 0 ? (
                            <p className="text-text-muted">No price near the date, left out: {p.dropped_no_price.join(", ")}</p>
                          ) : null}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        <ul className="space-y-0.5 text-xs text-text-muted">
          {r.caveats.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
        <p className="num text-xs text-text-faint">method {r.method_version} · run {r.fingerprint.slice(0, 10)}</p>
      </Blueprint>
    </>
  );
}
