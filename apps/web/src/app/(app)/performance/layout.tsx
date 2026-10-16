import type { Metadata } from "next";
import type { ReactNode } from "react";

import { PerformanceTabs } from "@/components/performance/performance-tabs";
import { TabPageHeader } from "@/components/tab-page-header";

export const metadata: Metadata = { title: "Performance" };

/** Each tab's own h1 (and, in its page.tsx, its own document title). */
const TITLES: Record<string, string> = {
  "/performance/attribution": "Contribution",
  "/performance/lots": "Lots and holding periods",
  "/performance/core-vs-stocks": "Core and stocks",
  "/performance/ledger": "Ledger and settings",
};

export default function PerformanceLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-5">
      <TabPageHeader
        kicker="Portfolio"
        fallback="Performance"
        titles={TITLES}
        description="How your investments did, how your own money did, and whether the ledger behind the figures holds up."
      />
      <PerformanceTabs />
      {children}
    </div>
  );
}
