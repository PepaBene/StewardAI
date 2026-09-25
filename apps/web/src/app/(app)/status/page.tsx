import type { Metadata } from "next";

import { JobLookup } from "@/components/jobs/job-lookup";
import { ReferenceGapsPanel } from "@/components/reference/reference-gaps";

import { HealthStatus } from "./health-status";

export const metadata: Metadata = { title: "API status" };

export default function StatusPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">API status</h1>
        <p className="mt-1 text-sm text-text-muted">
          Calls <code className="font-mono">GET /health</code> on the StewardAI Python API
          through the generated, typed client, opens a job run by its number, and lists the reference data gaps
          below.
        </p>
      </div>
      <HealthStatus />
      <JobLookup />
      <ReferenceGapsPanel />
    </div>
  );
}
