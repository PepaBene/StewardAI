import type { Metadata } from "next";
import { Suspense, type ReactNode } from "react";

import { TabPageHeader } from "@/components/tab-page-header";
import { XrayTabs } from "@/components/xray/xray-tabs";

export const metadata: Metadata = { title: "X-Ray" };

/** Each tab's own h1 (and, in its page.tsx, its own document title). */
const TITLES: Record<string, string> = {
  "/xray/overlap": "Overlap",
  "/xray/concentration": "Concentration",
  "/xray/diversification": "Diversification",
  "/xray/fundamentals": "Fundamentals",
  "/xray/benchmark": "Against a benchmark",
  "/xray/supply-chain": "Supply chain",
  "/xray/proposed": "What if",
  "/xray/inputs": "Your data",
};

export default function XrayLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-5">
      <TabPageHeader
        kicker="Portfolio"
        fallback="X-Ray"
        titles={TITLES}
        description="What you really own once funds are opened up: the same holdings seen by sector, country, issuer, fee and more, with what is unknown kept visible."
      />
      <Suspense>
        <XrayTabs />
      </Suspense>
      {children}
    </div>
  );
}
