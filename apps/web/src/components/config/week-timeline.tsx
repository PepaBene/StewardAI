"use client";

import { useState } from "react";

import { expandCron, wallClockNow, type CronRun } from "@/lib/config/cron";

export type TimelineJob = { name: string; label: string; cron: string; timezone: string; enabled: boolean };

const DAYS = 7;
const MINUTES = 1440;
const DENSE_AFTER = 120;
const WEEKDAY = new Intl.DateTimeFormat("en-GB", { weekday: "short", timeZone: "UTC" });

type Row = { job: TimelineJob; runs: CronRun[] | null; start: Date };

function pad(n: number) {
  return String(n).padStart(2, "0");
}

function timeOf(run: CronRun) {
  return `${pad(Math.floor(run.minute / 60))}:${pad(run.minute % 60)}`;
}

/**
 * The next seven days of every enabled schedule, one row per job, a tick at each run. Times are
 * in each schedule's own time zone. A schedule that runs more than a few times a day is drawn as
 * a band instead of ticks. The table below the chart carries the same facts as text.
 */
export function WeekTimeline({ jobs }: { jobs: readonly TimelineJob[] }) {
  const [now] = useState(() => new Date());
  const rows: Row[] = jobs
    .filter((j) => j.enabled)
    .map((job) => {
      const start = wallClockNow(job.timezone, now);
      return { job, start, runs: expandCron(job.cron, start, DAYS) };
    });
  if (rows.length === 0) return <p className="text-sm text-text-muted">No schedule is switched on.</p>;

  const head = rows[0].start;
  const dayLabels = Array.from({ length: DAYS }, (_, d) =>
    d === 0 ? "Today" : WEEKDAY.format(new Date(Date.UTC(head.getUTCFullYear(), head.getUTCMonth(), head.getUTCDate() + d))),
  );
  const summary = rows.map((r) => `${r.job.label}: ${r.runs ? `${r.runs.length} runs` : "invalid schedule"}`).join("; ");

  return (
    <div className="space-y-3">
      <div role="img" aria-label={`Runs over the next 7 days. ${summary}`} className="min-w-0 overflow-x-auto">
        <div className="min-w-[44rem] space-y-1">
          <div className="grid grid-cols-[11rem_1fr] items-end gap-3">
            <span />
            <div className="grid grid-cols-7 text-xs text-text-muted">
              {dayLabels.map((l, i) => (
                <span key={i} className="px-1">
                  {l}
                </span>
              ))}
            </div>
          </div>
          {rows.map(({ job, runs }) => (
            <div key={job.name} className="grid grid-cols-[11rem_1fr] items-center gap-3">
              <span className="truncate text-sm" title={job.label}>
                {job.label}
              </span>
              <div className="relative h-5 rounded-control bg-surface">
                {Array.from({ length: DAYS - 1 }, (_, i) => (
                  <span key={i} aria-hidden="true" className="absolute inset-y-0 w-px bg-chart-track" style={{ left: `${((i + 1) / DAYS) * 100}%` }} />
                ))}
                {runs === null ? (
                  <span className="absolute inset-0 flex items-center px-2 text-xs text-loss">Not a valid schedule</span>
                ) : runs.length > DENSE_AFTER ? (
                  <span aria-hidden="true" className="absolute inset-x-0 inset-y-1.5 rounded-full bg-accent/40" />
                ) : (
                  runs.map((r, i) => (
                    <span
                      key={i}
                      aria-hidden="true"
                      title={`${dayLabels[r.day]} ${timeOf(r)}`}
                      className="absolute inset-y-0.5 w-0.5 rounded-full bg-accent"
                      style={{ left: `${((r.day * MINUTES + r.minute) / (DAYS * MINUTES)) * 100}%` }}
                    />
                  ))
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-text-muted outline-none focus-visible:ring-2 focus-visible:ring-focus/30">Show as a table</summary>
        <table className="mt-2 w-full text-left text-sm">
          <caption className="sr-only">Runs in the next 7 days, per job</caption>
          <thead className="text-text-muted">
            <tr>
              <th scope="col" className="py-1 pr-3 font-medium">Job</th>
              <th scope="col" className="py-1 pr-3 font-medium">Time zone</th>
              <th scope="col" className="py-1 pr-3 text-right font-medium">Runs</th>
              <th scope="col" className="py-1 font-medium">Next run</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ job, runs }) => (
              <tr key={job.name} className="border-t border-border/50">
                <th scope="row" className="py-1 pr-3 font-normal">{job.label}</th>
                <td className="py-1 pr-3 text-text-muted">{job.timezone}</td>
                <td className="py-1 pr-3 text-right tabular-nums">{runs ? runs.length : "Invalid"}</td>
                <td className="py-1 tabular-nums">{runs && runs.length > 0 ? `${dayLabels[runs[0].day]} ${timeOf(runs[0])}` : "None this week"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
