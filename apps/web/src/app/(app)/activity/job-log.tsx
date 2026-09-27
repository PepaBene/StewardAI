"use client";

import { LoaderCircleIcon, RefreshCwIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { JobStatusBadge, JobStatusDot } from "@/components/jobs/job-status-badge";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type Holding, type JobList, type JobListItem } from "@/lib/api/client";
import {
  EMPTY_JOB_FILTERS,
  isJobFiltered,
  JOB_STATUSES,
  jobFiltersToSearch,
  jobLogQuery,
  jobTypeOptions,
  paramsSummary,
  parseJobFilters,
  runDuration,
  type JobLogFilters,
} from "@/lib/activity/activity";
import { formatDateTime, formatRelative } from "@/lib/format";
import { useNow } from "@/lib/hooks/use-now";
import { formatCost, JOB_STATUS, jobHref, jobTypeLabel } from "@/lib/jobs/job-detail";
import { latestOnly } from "@/lib/latest-request";
import { appendUnique } from "@/lib/portfolio/transactions";
import { cn } from "@/lib/utils";

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; jobs: JobListItem[]; next: number | null; more: "idle" | "loading" | string };

async function fetchJobs(filters: JobLogFilters, beforeId: number | null): Promise<JobList> {
  const { data, response } = await getBrowserApiClient().GET("/jobs", { params: { query: jobLogQuery(filters, beforeId) } });
  if (!data) throw new Error(`The API answered HTTP ${response.status}.`);
  return data;
}

/** Every scheduled, manual and event-triggered run, newest first, with its outcome and cost. */
export function JobLog() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters = useMemo(() => parseJobFilters(new URLSearchParams(params.toString())), [params]);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const now = useNow();
  // Only the newest load's answers are applied (filters can change while a page loads).
  const sequence = useRef(0);

  const setFilters = useCallback(
    (next: JobLogFilters) =>
      router.replace(`${pathname}${jobFiltersToSearch(next, new URLSearchParams(params.toString()))}#job-log`, { scroll: false }),
    [router, pathname, params],
  );

  useEffect(() => {
    const mine = ++sequence.current;
    fetchJobs(filters, null)
      .then((page) => mine === sequence.current && setLoad({ kind: "ok", jobs: page.jobs, next: page.next_before_id, more: "idle" }))
      .catch(
        (err: unknown) => mine === sequence.current && setLoad({ kind: "error", message: err instanceof Error ? err.message : String(err) }),
      );
    return () => {
      sequence.current += 1;
    };
  }, [filters, reloadKey]);

  useEffect(() => {
    getBrowserApiClient()
      .GET("/portfolio/holdings")
      .then((r) => r.data && setHoldings(r.data.holdings))
      .catch(() => undefined);
  }, []);

  async function loadMore() {
    if (load.kind !== "ok" || load.next === null || load.more === "loading") return;
    const beforeId = load.next;
    setLoad({ ...load, more: "loading" });
    const outcome = await latestOnly(sequence, () => fetchJobs(filters, beforeId));
    if (outcome.stale) return;
    setLoad((prev) => {
      if (prev.kind !== "ok") return prev;
      if (!outcome.ok) return { ...prev, more: outcome.error };
      return { kind: "ok", jobs: appendUnique(prev.jobs, outcome.value.jobs), next: outcome.value.next_before_id, more: "idle" };
    });
  }

  const types = jobTypeOptions([...filters.types, ...(load.kind === "ok" ? load.jobs.map((j) => j.job_type) : [])]);
  const chip = (active: boolean) =>
    cn(
      "inline-flex cursor-pointer items-center gap-1.5 rounded-md border px-2 py-1 text-sm has-focus-visible:ring-2 has-focus-visible:ring-focus",
      active ? "border-accent bg-surface-muted font-medium text-text" : "border-border text-text-muted hover:text-text",
    );

  return (
    <section id="job-log" aria-labelledby="job-log-heading" className="scroll-mt-24 space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="job-log-heading" className="text-lg font-semibold">
            Job log
          </h2>
          <p className="text-sm text-text-muted">
            Newest first. Cost is the sum of the run&rsquo;s model and search steps; open a run for its steps and errors.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
          <RefreshCwIcon aria-hidden="true" />
          Reload
        </Button>
      </div>

      <div className="flex flex-wrap items-start gap-x-6 gap-y-3 rounded-lg border border-border bg-surface-raised p-3">
        <label className="space-y-1 text-sm">
          <span className="block font-medium">Job type</span>
          <select
            value={filters.types.length === 1 ? filters.types[0] : filters.types.length > 1 ? "__many" : ""}
            onChange={(e) => setFilters({ ...filters, types: e.target.value && e.target.value !== "__many" ? [e.target.value] : [] })}
            className="rounded-md border border-border bg-surface-raised px-2 py-1 text-sm text-text"
          >
            <option value="">All types</option>
            {filters.types.length > 1 ? <option value="__many">{filters.types.map(jobTypeLabel).join(", ")}</option> : null}
            {types.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="space-y-1">
          <legend className="text-sm font-medium">Status</legend>
          <div className="flex flex-wrap gap-1">
            {JOB_STATUSES.map((s) => {
              const active = filters.statuses.includes(s);
              return (
                <label key={s} className={chip(active)}>
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={active}
                    onChange={() =>
                      setFilters({ ...filters, statuses: active ? filters.statuses.filter((x) => x !== s) : [...filters.statuses, s] })
                    }
                  />
                  {JOB_STATUS[s].label}
                </label>
              );
            })}
          </div>
        </fieldset>
        <label className="space-y-1 text-sm">
          <span className="block font-medium">Holding</span>
          <select
            value={filters.ticker ?? ""}
            onChange={(e) => setFilters({ ...filters, ticker: e.target.value || null })}
            className="rounded-md border border-border bg-surface-raised px-2 py-1 text-sm text-text"
          >
            <option value="">All (and runs for no holding)</option>
            {filters.ticker && !holdings.some((h) => h.ticker === filters.ticker) ? <option value={filters.ticker}>{filters.ticker}</option> : null}
            {holdings.map((h) => (
              <option key={h.ticker} value={h.ticker}>
                {h.name ?? h.ticker}
              </option>
            ))}
          </select>
        </label>
        {isJobFiltered(filters) ? (
          <Button variant="ghost" size="sm" className="self-end" onClick={() => setFilters(EMPTY_JOB_FILTERS)}>
            <XIcon aria-hidden="true" />
            Clear filters
          </Button>
        ) : null}
        {filters.ticker ? (
          <p className="basis-full text-xs text-text-muted">
            Only runs started for {filters.ticker} (earnings checks, verdict runs, reference refreshes, impact notes) have a holding.
          </p>
        ) : null}
      </div>

      {load.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading the job log…
        </p>
      ) : null}
      {load.kind === "error" ? <AlertMessage title="Could not load the job log">{load.message}</AlertMessage> : null}
      {load.kind === "ok" && load.jobs.length === 0 ? (
        <EmptyState title={isJobFiltered(filters) ? "No run matches these filters" : "No runs yet"}>
          {isJobFiltered(filters) ? "Clear a filter to see more." : "Scheduled runs appear once the worker is running."}
        </EmptyState>
      ) : null}
      {load.kind === "ok" && load.jobs.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
          <table className="w-full min-w-[56rem] text-sm">
            <caption className="sr-only">Job runs, newest first</caption>
            <thead className="bg-surface-muted">
              <tr className="border-b border-border text-text-muted">
                <th scope="col" className="px-3 py-2 text-left font-medium">Run</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Status</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Queued</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Duration</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Cost</th>
                <th scope="col" className="px-3 py-2 text-left font-medium">Failed step / error</th>
              </tr>
            </thead>
            <tbody>
              {load.jobs.map((j) => {
                const summary = paramsSummary(j.params);
                return (
                  <tr key={j.id} className="border-b border-border align-top last:border-0">
                    <th scope="row" className="px-3 py-2 text-left font-normal">
                      <Link href={jobHref(j.id)} className="flex items-center gap-2 font-medium hover:text-accent hover:underline">
                        <JobStatusDot status={j.status} />
                        {jobTypeLabel(j.job_type)} <span className="num text-text-muted">#{j.id}</span>
                      </Link>
                      <span className="block text-xs text-text-muted">
                        {j.trigger === "manual" ? "by hand" : j.trigger}
                        {summary ? ` · ${summary}` : ""}
                      </span>
                    </th>
                    <td className="px-3 py-2">
                      <JobStatusBadge status={j.status} />
                    </td>
                    <td className="num px-3 py-2 whitespace-nowrap">
                      <time dateTime={j.created_at} title={formatDateTime(j.created_at)}>
                        {formatRelative(new Date(j.created_at), now)}
                      </time>
                    </td>
                    <td className="num px-3 py-2 text-right whitespace-nowrap">{runDuration(j, now)}</td>
                    <td className="num px-3 py-2 text-right whitespace-nowrap" title={j.cost_usd === null ? "No cost recorded" : `${j.cost_usd} USD`}>
                      {formatCost(j.cost_usd)}
                    </td>
                    <td className="max-w-md px-3 py-2 text-xs">
                      {j.failed_step ? <span className="font-mono font-medium">{j.failed_step}</span> : null}
                      {j.error ? <span className="block break-words text-text-muted">{j.error}</span> : null}
                      {!j.failed_step && !j.error ? <span className="text-text-muted">—</span> : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {load.kind === "ok" && load.next !== null ? (
        <div className="flex items-center gap-3">
          <Button variant="outline" onClick={() => void loadMore()} disabled={load.more === "loading"}>
            {load.more === "loading" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
            Load older runs
          </Button>
          {load.more !== "idle" && load.more !== "loading" ? <span className="text-sm text-text">{load.more}</span> : null}
        </div>
      ) : null}
    </section>
  );
}
