"use client";

import { KpiStrip } from "@/components/insight/kpi-tile";
import { PageHeader } from "@/components/page-header";
import { getBrowserApiClient } from "@/lib/api/client";
import { needsAttention } from "@/lib/data-sources/data-sources";
import { useApiResource } from "@/lib/hooks/use-api-resource";

import { AccessPanel } from "./access-panel";
import { BudgetPanel } from "./budget-panel";
import { CoverageMatrixPanel } from "./coverage-matrix-panel";
import { CoverageSummaryPanel } from "./coverage-summary-panel";
import { JobTriggersPanel } from "./job-triggers-panel";
import { TrailPanel } from "./trail-panel";
import { UsagePanel } from "./usage-panel";

const SECTIONS = [
  { id: "matrix", label: "What each source covers" },
  { id: "budget", label: "Free quota used" },
  { id: "access", label: "Endpoint access" },
  { id: "usage", label: "Usage over time" },
  { id: "coverage-summary", label: "Coverage in numbers" },
  { id: "trail", label: "Alert decisions" },
  { id: "run-job", label: "Run a job now" },
] as const;

const fetchAccess = () => getBrowserApiClient().GET("/sources/access", { params: { query: {} } });

/** The answer strip: how many endpoints work, need a decision, or are switched off. */
function AccessStrip() {
  const { state } = useApiResource(fetchAccess);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const total = data?.length ?? 0;
  const attention = data ? data.filter(needsAttention).length : 0;
  const blocked = data ? data.filter((a) => a.hard_blocked).length : 0;
  const working = data ? data.filter((a) => a.last_success_at && !needsAttention(a)).length : 0;
  const none = data ? undefined : "Loading…";
  return (
    <KpiStrip
      label="Data sources at a glance"
      tiles={[
        {
          label: "Endpoints working",
          value: data ? `${working} of ${total}` : "—",
          verdict: { tone: !data ? "unknown" : working === total ? "good" : "caution", text: none ?? "Succeeded recently and never refused." },
        },
        {
          label: "Need a decision",
          value: data ? attention : "—",
          verdict: { tone: !data ? "unknown" : attention === 0 ? "good" : "caution", text: none ?? (attention === 0 ? "Nothing is waiting." : "Refused, never worked or switched off. See Endpoint access.") },
        },
        {
          label: "Switched off",
          value: data ? blocked : "—",
          verdict: { tone: !data ? "unknown" : blocked === 0 ? "good" : "caution", text: none ?? (blocked === 0 ? "No endpoint is blocked." : "Not retried until you reset them.") },
        },
        {
          label: "Paid plans used",
          value: "None",
          verdict: { tone: "good", text: "Only free-tier endpoints are ever called." },
        },
      ]}
    />
  );
}

/**
 * Data sources: which free providers actually return data for your holdings, how much of
 * each free quota is spent, and which endpoints are denied. Missing data is shown as missing,
 * never as zero.
 */
export function DataSourcesView() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Data sources"
        description="Which free providers return data for your holdings, how much of each free quota is spent, and which endpoints are denied or untested. Nothing here ever uses a paid plan."
      />
      <AccessStrip />
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
      <CoverageSummaryPanel />
      <TrailPanel />
      <JobTriggersPanel />
    </div>
  );
}
