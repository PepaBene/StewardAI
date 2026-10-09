import type { Metadata } from "next";
import type { ReactNode } from "react";

import { InsightTabs } from "@/components/insights/insight-tabs";
import { PageHeader } from "@/components/page-header";

export const metadata: Metadata = { title: "What changed" };

export default function InsightsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Research"
        title="What changed"
        description="What the app knows now against what it knew at an earlier date, and how the companies it covers compare by sector. Every item names the records behind it."
      />
      <InsightTabs />
      {children}
    </div>
  );
}
