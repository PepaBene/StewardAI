"use client";

import { ActivityIcon } from "lucide-react";
import Link from "next/link";

import { AlertsPanel } from "./alerts-panel";
import { CoveragePanel } from "./coverage-panel";
import { EvalCandidatesPanel } from "./eval-candidates-panel";
import { JobLog } from "./job-log";
import { TrustPanel } from "./trust-panel";

const SECTIONS = [
  { id: "coverage", label: "Coverage" },
  { id: "alerts", label: "Alerts" },
  { id: "job-log", label: "Job log" },
  { id: "source-trust", label: "Source trust" },
  { id: "eval-candidates", label: "Eval candidates" },
] as const;

/**
 * Activity: what ran, what it cost and what it missed. Coverage gaps and monitoring alerts
 * come first because they are what needs attention; the job log, source trust and the
 * evaluation candidates added from feedback follow.
 */
export function ActivityView() {
  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">Activity</h1>
          <p className="mt-1 max-w-prose text-sm text-text-muted">
            Per-holding coverage gaps, missed runs and failed jobs, the job log with cost, how far each source is trusted
            after your feedback, and the items you added to the evaluation sets.
          </p>
        </div>
        <Link href="/status" className="inline-flex items-center gap-1 text-sm text-accent hover:underline">
          <ActivityIcon aria-hidden="true" className="size-4" />
          API health and reference data gaps
        </Link>
      </div>
      <nav aria-label="On this page">
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {SECTIONS.map((s) => (
            <li key={s.id}>
              <a href={`#${s.id}`} className="text-accent hover:underline">
                {s.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <CoveragePanel />
      <AlertsPanel />
      <JobLog />
      <TrustPanel />
      <EvalCandidatesPanel />
    </div>
  );
}
