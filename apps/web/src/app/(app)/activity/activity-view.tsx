"use client";

import { ActivityIcon } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/page-header";

import { AlertsPanel } from "./alerts-panel";
import { CoveragePanel } from "./coverage-panel";
import { EvalCandidatesPanel } from "./eval-candidates-panel";
import { JobLog } from "./job-log";
import { CoverageRings, JobGridPanel, SpendPanel } from "./overview-panels";
import { TrustPanel } from "./trust-panel";

const SECTIONS = [
  { id: "coverage", label: "Coverage per holding" },
  { id: "alerts", label: "Alerts" },
  { id: "job-log", label: "Job log" },
] as const;

/**
 * Activity: whether every job ran, what it cost and what it covered. The overview (coverage
 * tiles, the 14-day run matrix, the month's AI spend and cost by job) comes first; then the
 * per-holding coverage table, monitoring alerts and the job log, with source trust and the
 * evaluation candidates folded away.
 */
export function ActivityView() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Activity"
        description="Whether every job ran, what it cost and what it covered: coverage gaps, missed and failed runs, source trust after your feedback, and the items you added to the evaluation sets."
        aside={
          <Link href="/status" className="inline-flex items-center gap-1 text-sm text-accent-ink hover:underline">
            <ActivityIcon aria-hidden="true" className="size-4" />
            API health and reference data gaps
          </Link>
        }
      />

      <CoverageRings />

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)]">
        <JobGridPanel />
        <SpendPanel />
      </div>

      <nav aria-label="On this page" className="border-y border-border-soft py-2">
        <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          {SECTIONS.map((s) => (
            <li key={s.id}>
              <a href={`#${s.id}`} className="text-accent-ink hover:underline">
                {s.label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <CoveragePanel />
      <AlertsPanel />
      <JobLog />
      <details className="group rounded-card bg-surface-muted px-5 py-3">
        <summary className="cursor-pointer text-[15px] font-semibold">Source trust and evaluation candidates</summary>
        <div className="mt-3 flex flex-col gap-5">
          <TrustPanel />
          <EvalCandidatesPanel />
        </div>
      </details>
    </div>
  );
}
