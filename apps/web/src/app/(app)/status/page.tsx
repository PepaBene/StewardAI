import type { Metadata } from "next";

import { HealthStatus } from "./health-status";

export const metadata: Metadata = { title: "API status" };

export default function StatusPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">API status</h1>
        <p className="mt-1 text-sm text-text-muted">
          Calls <code className="font-mono">GET /health</code> on the StewardAI Python API
          through the generated, typed client.
        </p>
      </div>
      <HealthStatus />
    </div>
  );
}
