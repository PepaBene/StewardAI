import type { Metadata } from "next";
import type { ReactNode } from "react";

import { RiskTabs } from "@/components/risk/risk-tabs";
import { TabPageHeader } from "@/components/tab-page-header";

export const metadata: Metadata = { title: "Risk and decisions" };

/** Each tab's own h1 (and, in its page.tsx, its own document title). */
const TITLES: Record<string, string> = {
  "/risk/ex-ante": "Today’s risk",
  "/risk/tail": "Bad months",
  "/risk/replay": "Past episodes",
  "/risk/factors": "Factors",
  "/risk/stress": "Stress test",
  "/risk/themes": "Themes",
  "/risk/whatif": "What if",
  "/risk/rebalance": "Rebalance",
  "/risk/pretrade": "Before a trade",
  "/risk/deposit": "Size and deposit",
  "/risk/validation": "Does it hold up",
};

export default function RiskLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-5">
      <TabPageHeader
        kicker="Portfolio"
        fallback="Risk and decisions"
        titles={TITLES}
        description="How the portfolio has behaved, what a bad scenario would cost, and what a trade would change before you place it. Nothing here sends an order."
      />
      <RiskTabs />
      {children}
    </div>
  );
}
