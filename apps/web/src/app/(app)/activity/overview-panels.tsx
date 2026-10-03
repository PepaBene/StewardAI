"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ChartEmpty, Gauge, GridStatus, HBar, Ring } from "@/components/charts";
import { Blueprint, BlueprintCorners, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient, type JobList, type JobListItem } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import {
  earningsCoverage,
  firstTimeRate,
  GRID_DAYS,
  gridDays,
  gridStart,
  identityCoverage,
  jobGrid,
  lastRunNote,
  priceCoverage,
  sourceCoverage,
  spend,
  type CoverageRatio,
} from "@/lib/activity/overview";
import { formatPct } from "@/lib/charts/format";
import { useIdentityReview, usePriceCoverage } from "@/lib/coverage/hooks";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { useNow } from "@/lib/hooks/use-now";
import { formatCost, jobTypeLabel } from "@/lib/jobs/job-detail";
import { shortDay } from "@/lib/news/stats";

type CostSummary = components["schemas"]["CostSummary"];

const fetchCosts = () => getBrowserApiClient().GET("/costs/summary");
const fetchCoverage = () => getBrowserApiClient().GET("/activity/coverage");
const fetchHealth = () => getBrowserApiClient().GET("/news/sources/health");

const JOB_PAGE_LIMIT = 200;
const MAX_PAGES = 10;

function monthName(summary: CostSummary): string {
  return new Intl.DateTimeFormat("en-US", { month: "long", timeZone: "Europe/Prague" }).format(new Date(summary.period_start));
}

/** The month's model and search spend against the cap, by model and by job type. */
export function SpendPanel() {
  const { state } = useApiResource(fetchCosts);
  const now = useNow();
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  return (
    <Blueprint aria-labelledby="spend-heading" className="flex flex-col gap-4 px-5 py-5">
      <PanelHeading
        kicker={data ? `AI spend · ${monthName(data)}` : "AI spend"}
        title={<span id="spend-heading">{data ? (data.cap_usd !== null ? `Budget ${formatCost(data.cap_usd)}` : "No monthly cap set") : "This month"}</span>}
        action={
          <Link href="/config/budget" className="text-xs text-accent-ink hover:underline">
            Budget
          </Link>
        }
      />
      {state.kind === "loading" ? <ChartEmpty label="Loading spend…" minHeight={140} /> : null}
      {state.kind === "error" && !data ? <ChartEmpty label={`Could not load spend: ${state.title}`} minHeight={140} /> : null}
      {data ? <SpendBody data={data} now={now} /> : null}
    </Blueprint>
  );
}

function SpendBody({ data, now }: { data: CostSummary; now: Date }) {
  const s = spend(data, now);
  const max = Math.max(data.total_usd, 1e-9);
  const top = <T extends { cost_usd: number }>(rows: T[]) => [...rows].sort((a, b) => b.cost_usd - a.cost_usd).slice(0, 4);
  return (
    <div className="grid grid-cols-1 items-center gap-5 sm:grid-cols-[minmax(0,13rem)_minmax(0,1fr)]">
      <div>
        {data.cap_usd !== null ? (
          <Gauge
            value={data.total_usd}
            max={data.cap_usd}
            label={formatCost(data.total_usd)}
            sublabel={data.fraction !== null ? `${formatPct(data.fraction, 0)} of the cap` : undefined}
            tone={data.fraction !== null && data.fraction >= data.warn_at_fraction ? "warning" : "accent"}
            ariaLabel={`AI spend this month against the ${formatCost(data.cap_usd)} cap`}
            formatValue={(v) => formatCost(v)}
          />
        ) : (
          <p className="num font-heading text-[34px] leading-none font-semibold">{formatCost(data.total_usd)}</p>
        )}
        <p className="mt-1 text-center text-[11px] text-text-muted">
          {s.projected !== null ? (
            <>
              Projection, linear: <span className="num font-medium text-text">{formatCost(s.projected)}</span> by{" "}
              {shortDay(new Date(Date.parse(data.period_end) - 1))}
              {s.projectedOver ? " (over the cap)" : ""}
            </>
          ) : (
            "Too early in the month to project."
          )}
          <span className="block">
            {formatPct(s.elapsed, 0)} of the month elapsed; at the cap: {data.at_cap.replaceAll("_", " ")}
          </span>
        </p>
      </div>
      <div className="flex min-w-0 flex-col gap-4 text-xs">
        <SpendList title="By model" rows={top(data.by_model).map((m) => ({ key: m.model, label: m.model, sub: `${m.steps} steps`, cost: m.cost_usd }))} max={max} mono />
        <SpendList
          title="By purpose"
          rows={top(data.by_job_type).map((j) => ({
            key: j.job_type,
            label: jobTypeLabel(j.job_type),
            sub: `${j.runs} runs${j.critical ? " · critical" : ""}`,
            cost: j.cost_usd,
          }))}
          max={max}
        />
      </div>
    </div>
  );
}

function SpendList({
  title,
  rows,
  max,
  mono = false,
}: {
  title: string;
  rows: { key: string; label: string; sub: string; cost: number }[];
  max: number;
  mono?: boolean;
}) {
  return (
    <div>
      <p className="kicker mb-1.5">{title}</p>
      {rows.length === 0 ? <p className="text-text-muted">Nothing spent yet.</p> : null}
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {rows.map((r) => (
          <li key={r.key} className="space-y-1">
            <span className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 truncate">
                <span className={mono ? "font-mono text-[11px]" : undefined}>{r.label}</span>{" "}
                <span className="text-text-muted">· {r.sub}</span>
              </span>
              <span className="num font-semibold">{formatCost(r.cost)}</span>
            </span>
            <HBar value={r.cost} max={max} size="sm" label={`${r.label} spend`} formatValue={(v) => formatCost(v)} />
          </li>
        ))}
      </ul>
    </div>
  );
}

type JobsLoad = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; jobs: JobListItem[]; truncated: boolean };

/** Every run of the last 21 days (paged by before_id), for the grid. */
function useWindowJobs(days: string[]): JobsLoad {
  const [load, setLoad] = useState<JobsLoad>({ kind: "loading" });
  const since = gridStart(days);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const jobs: JobListItem[] = [];
      let before: number | null = null;
      let truncated = false;
      try {
        for (let page = 0; page < MAX_PAGES; page++) {
          const { data, response }: { data?: JobList; response: Response } = await getBrowserApiClient().GET("/jobs", {
            params: { query: { limit: JOB_PAGE_LIMIT, before_id: before ?? undefined } },
          });
          if (!data) throw new Error(`The API answered HTTP ${response.status}.`);
          jobs.push(...data.jobs);
          const oldest = data.jobs[data.jobs.length - 1];
          if (data.next_before_id === null || !oldest || oldest.created_at < since) break;
          before = data.next_before_id;
          if (page === MAX_PAGES - 1) truncated = true;
        }
        if (!cancelled) setLoad({ kind: "ok", jobs, truncated });
      } catch (err) {
        if (!cancelled) setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [since]);
  return load;
}

/** Job types × days: ok, retried, failed or not scheduled, with each type's last run. */
export function JobGridPanel() {
  const now = useNow();
  const today = now.toISOString().slice(0, 10);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- recompute the window once per day, not every tick
  const days = useMemo(() => gridDays(now, GRID_DAYS), [today]);
  const load = useWindowJobs(days);
  const inWindow = load.kind === "ok" ? load.jobs.filter((j) => j.created_at >= gridStart(days)) : [];
  const rows = jobGrid(inWindow, days);
  const rate = firstTimeRate(inWindow);
  return (
    <Blueprint aria-labelledby="job-grid-heading" className="flex flex-col gap-3 px-5 py-5">
      <PanelHeading
        kicker="Job runs"
        title={
          <span id="job-grid-heading" className="num">
            Last {GRID_DAYS} days{rate ? ` · ${formatPct(rate.rate, 1)} ran first time` : ""}
          </span>
        }
        action={
          <a href="#job-log" className="text-xs text-accent-ink hover:underline">
            Every run
          </a>
        }
      />
      {load.kind === "loading" ? <ChartEmpty label="Loading runs…" minHeight={200} /> : null}
      {load.kind === "error" ? <ChartEmpty label={`Could not load runs: ${load.message}`} minHeight={120} /> : null}
      {load.kind === "ok" ? (
        <div className="relative overflow-x-auto">
          <GridStatus
            className="min-w-[44rem]"
            caption={`Job runs by type and day, last ${GRID_DAYS} days`}
            columns={days.map((d) => shortDay(new Date(`${d}T12:00:00Z`)))}
            rows={rows.map((r) => ({
              label: jobTypeLabel(r.jobType),
              cells: r.cells,
              note: r.last ? lastRunNote(r.last, now) : undefined,
            }))}
          />
        </div>
      ) : null}
      {load.kind === "ok" ? (
        <p className="text-xs text-text-muted">
          A day is <em>retried</em> when a run needed more than one attempt, <em>failed</em> when any run failed. &ldquo;Ran first
          time&rdquo;: finished runs that succeeded on their first attempt
          {rate ? ` (${rate.finished} finished)` : ""}. The note is each type&rsquo;s last run and how long it took.
          {load.truncated ? " Only the newest runs were loaded; the oldest days may be incomplete." : ""}
        </p>
      ) : null}
    </Blueprint>
  );
}

/** Prices, earnings checks, news sources and identity coverage as four rings. */
export function CoverageRings() {
  const coverage = useApiResource(fetchCoverage).state;
  const health = useApiResource(fetchHealth).state;
  const priceCov = usePriceCoverage().state;
  const identity = useIdentityReview().state;
  const cov = coverage.kind === "ok" ? coverage.data : null;
  const rings: { title: string; ratio: CoverageRatio | null; loading: boolean; href: string }[] = [
    { title: "Prices", ratio: priceCov.kind === "ok" ? priceCoverage(priceCov.data) : null, loading: priceCov.kind === "loading", href: "/coverage#price-coverage" },
    { title: "Earnings checks", ratio: cov ? earningsCoverage(cov) : null, loading: coverage.kind === "loading", href: "#coverage" },
    {
      title: "News sources",
      ratio: health.kind === "ok" ? sourceCoverage(health.data) : null,
      loading: health.kind === "loading",
      href: "/config/news-sources",
    },
    {
      title: "Identity",
      ratio: identity.kind === "ok" ? identityCoverage(identity.data) : null,
      loading: identity.kind === "loading",
      href: "/coverage#identity-review",
    },
  ];
  return (
    <section aria-label="Coverage" className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
      {rings.map((r) => {
        const pct = r.ratio && r.ratio.total > 0 ? (r.ratio.share ?? r.ratio.value / r.ratio.total) : null;
        return (
          <div key={r.title} className="blueprint flex items-center gap-4 px-5 py-4">
            <BlueprintCorners />
            <Ring
              value={pct === null ? null : pct * 100}
              label={pct === null ? "—" : `${Math.round(pct * 100)}%`}
              size={58}
              thickness={4.5}
              ariaLabel={`${r.title} coverage`}
            />
            <div className="min-w-0">
              <h3 className="text-lg leading-tight font-semibold">
                <Link href={r.href} className="hover:underline">
                  {r.title}
                </Link>
              </h3>
              <p className="text-xs text-text-muted">
                {r.ratio ? (
                  <>
                    <span className="num">{r.ratio.figure ?? `${r.ratio.value} of ${r.ratio.total}`}</span>{" "}
                    · {r.ratio.note}
                  </>
                ) : r.loading ? (
                  "Loading…"
                ) : (
                  "Unavailable"
                )}
              </p>
            </div>
          </div>
        );
      })}
    </section>
  );
}
