"use client";

import { useState } from "react";

import { QueuedJob, SaveNote, useMutation } from "@/components/reference/action-bits";
import { Field } from "@/components/reference/form-bits";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { JOBS, jobTakesTicker, type JobPath } from "@/lib/reference/endpoint-forms";

/**
 * POST /jobs/...: queue one background job now instead of waiting for its schedule. The queued
 * job links to its page. Jobs that call a provider spend that provider's free-tier quota.
 */
export function JobTriggersPanel() {
  return (
    <section id="run-job" aria-labelledby="run-job-heading" className="blueprint scroll-mt-24 space-y-3 px-5 py-5">
      <BlueprintCorners />
      <h2 id="run-job-heading" className="text-[22px] leading-tight font-semibold">
        Run a job now
      </h2>
      <p className="max-w-[68ch] text-sm text-text-muted">
        Jobs normally run on their schedule. Running one by hand queues it once; it may call providers and use their free quota, and a job already running
        is not duplicated.
      </p>
      <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {JOBS.map((j) => (
          <JobCard key={j.path} path={j.path} label={j.label} note={j.note} />
        ))}
      </ul>
    </section>
  );
}

function JobCard({ path, label, note }: { path: JobPath; label: string; note: string }) {
  const { state, run } = useMutation();
  const [queued, setQueued] = useState<unknown>(null);
  const [ticker, setTicker] = useState("");
  const [force, setForce] = useState(false);
  const takesTicker = jobTakesTicker(path);
  const tickerOk = ticker.trim() === "" || /^[A-Za-z0-9._-]{1,32}$/.test(ticker.trim());
  const t = ticker.trim() === "" ? undefined : ticker.trim();

  function start() {
    if (!tickerOk) return;
    const api = getBrowserApiClient();
    const call =
      path === "/jobs/sec-xbrl"
        ? () => api.POST("/jobs/sec-xbrl", { body: { ...(t ? { ticker: t } : {}), force } })
        : path === "/jobs/fact-reconcile"
          ? () => api.POST("/jobs/fact-reconcile", { body: t ? { ticker: t } : {} })
          : () => api.POST(path as Exclude<JobPath, "/jobs/sec-xbrl" | "/jobs/fact-reconcile">);
    void run(call, () => `${label} queued.`, setQueued);
  }

  return (
    <li className="space-y-2 border border-border bg-surface p-3">
      <div>
        <h3 className="text-base font-semibold">{label}</h3>
        <p className="text-xs text-text-muted">{note}</p>
      </div>
      {takesTicker ? (
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Only this ticker" hint="Blank runs every holding" error={tickerOk ? undefined : "Letters, digits, dots, dashes, underscores."} className="w-44">
            <Input value={ticker} onChange={(e) => setTicker(e.target.value)} maxLength={32} />
          </Field>
          {path === "/jobs/sec-xbrl" ? (
            <label className="flex items-center gap-2 pb-2 text-sm">
              <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
              Force a re-fetch
            </label>
          ) : null}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" size="sm" disabled={state.kind === "saving" || !tickerOk} onClick={start}>
          Run {label.toLowerCase()}
        </Button>
        <SaveNote state={state} />
      </div>
      <QueuedJob data={queued} />
    </li>
  );
}
