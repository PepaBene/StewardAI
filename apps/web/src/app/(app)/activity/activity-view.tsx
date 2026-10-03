"use client";

import { ActivityIcon } from "lucide-react";
import Link from "next/link";

import { PageHeader } from "@/components/page-header";

import { AlertsPanel } from "./alerts-panel";
import { CoveragePanel } from "./coverage-panel";
import { SourceCoveragePanel } from "./source-coverage-panel";
import { DecisionTrailPanel } from "./decision-trail-panel";
import { EvalCandidatesPanel } from "./eval-candidates-panel";
import { JobLog } from "./job-log";
import { CoverageRings, JobGridPanel, SpendPanel } from "./overview-panels";
import { TrustPanel } from "./trust-panel";

const SECTIONS = [
  { id: "coverage", label: "Coverage per holding" },
  { id: "source-coverage", label: "Source coverage" },
  { id: "alerts", label: "Alerts" },
  { id: "decision-trail", label: "Decision trail" },
  { id: "job-log", label: "Job log" },
  { id: "source-trust", label: "Source trust" },
  { id: "eval-candidates", label: "Eval candidates" },
] as const;

/**
 * Activity: whether every job ran, what it cost and what it covered. The overview (coverage
 * rings, the 21-day run grid, the month's AI spend) comes first; then the per-holding
 * coverage table, monitoring alerts, the job log, source trust and the evaluation
 * candidates added from feedback.
 */
export function ActivityView() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Oversight"
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
      <SourceCoveragePanel />
      <AlertsPanel />
      <DecisionTrailPanel />
      <JobLog />
      <TrustPanel />
      <EvalCandidatesPanel />
    </div>
  );
}
