import type { Metadata } from "next";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/page-header";
import { RiskTabs } from "@/components/risk/risk-tabs";

export const metadata: Metadata = { title: "Risk" };

export default function RiskLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Portfolio"
        title="Risk and decisions"
        description="How the portfolio has behaved, what a bad scenario would cost, and what a trade would change before you place it. Nothing here sends an order."
      />
      <RiskTabs />
      {children}
    </div>
  );
}
