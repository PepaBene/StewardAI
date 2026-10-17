"use client";

import { useSearchParams } from "next/navigation";

import { DigestShell, type DigestPeriod } from "@/components/reports/digest-shell";

import { DigestDetail } from "./[id]/digest-view";

/**
 * /reports: the digests of one period (daily by default; ?period=weekly, monthly or quarterly) beside the latest
 * one; full-text search over every report sits under the header.
 */
export function ReportsView() {
  const params = useSearchParams();
  const asked = params.get("period");
  const period: DigestPeriod = asked === "weekly" || asked === "monthly" || asked === "quarterly" ? asked : "daily";
  return (
    <DigestShell period={period} selectedId="latest">
      {(reports) => (reports && reports.length > 0 ? <DigestDetail id={reports[0].id} /> : null)}
    </DigestShell>
  );
}
