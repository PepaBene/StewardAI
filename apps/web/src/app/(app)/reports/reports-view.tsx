"use client";

import { useSearchParams } from "next/navigation";

import { DigestShell, type DigestPeriod } from "@/components/reports/digest-shell";

import { DigestDetail } from "./[id]/digest-view";

/**
 * /reports: the digests of one period (daily by default, ?period=weekly) beside the latest
 * one; full-text search over every report sits under the header.
 */
export function ReportsView() {
  const params = useSearchParams();
  const period: DigestPeriod = params.get("period") === "weekly" ? "weekly" : "daily";
  return (
    <DigestShell period={period} selectedId="latest">
      {(reports) => (reports && reports.length > 0 ? <DigestDetail id={reports[0].id} /> : null)}
    </DigestShell>
  );
}
