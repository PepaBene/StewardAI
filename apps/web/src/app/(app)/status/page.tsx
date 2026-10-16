import type { Metadata } from "next";
import Link from "next/link";

import { JobLookup } from "@/components/jobs/job-lookup";
import { PageHeader } from "@/components/page-header";
import { ReferenceGapsPanel } from "@/components/reference/reference-gaps";
import { Blueprint } from "@/components/ui/blueprint";

import { HealthStatus } from "./health-status";

export const metadata: Metadata = { title: "API status" };

export default function StatusPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="API status"
        description={
          <p className="max-w-prose">
            Whether the server behind StewardAI is running, a lookup for one job run by its number, and the reference data gaps below. The job log,
            coverage gaps, monitoring alerts and source trust are on the{" "}
            <Link href="/activity" className="text-accent-ink hover:underline">
              Activity
            </Link>{" "}
            page.
          </p>
        }
      />
      <div className="grid items-start gap-5 lg:grid-cols-2">
        <HealthStatus />
        <Blueprint className="p-5 [&>section]:max-w-none">
          <JobLookup />
        </Blueprint>
      </div>
      <Blueprint className="p-5 [&>section]:max-w-none">
        <ReferenceGapsPanel />
      </Blueprint>
    </div>
  );
}
