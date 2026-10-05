"use client";

import { PageHeader } from "@/components/page-header";

import { AccessPanel } from "./access-panel";
import { BudgetPanel } from "./budget-panel";
import { CoverageMatrixPanel } from "./coverage-matrix-panel";
import { UsagePanel } from "./usage-panel";

const SECTIONS = [
  { id: "matrix", label: "What each source covers" },
  { id: "budget", label: "Free quota used" },
  { id: "access", label: "Endpoint access" },
  { id: "usage", label: "Usage over time" },
] as const;

/**
 * Data sources: which free providers actually return data for your holdings, how much of
 * each free quota is spent, and which endpoints are denied. Missing data is shown as missing,
 * never as zero.
 */
export function DataSourcesView() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Oversight"
        title="Data sources"
        description="Which free providers return data for your holdings, how much of each free quota is spent, and which endpoints are denied or untested. Nothing here ever uses a paid plan."
      />
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
      <CoverageMatrixPanel />
      <BudgetPanel />
      <AccessPanel />
      <UsagePanel />
    </div>
  );
}
