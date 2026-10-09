import type { Metadata } from "next";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import { PerformanceTabs } from "@/components/performance/performance-tabs";

export const metadata: Metadata = { title: "Performance" };

export default function PerformanceLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Portfolio"
        title="Performance"
        description="How your investments did, how your own money did, and whether the ledger behind the figures holds up."
      />
      <PerformanceTabs />
      {children}
    </div>
  );
}
