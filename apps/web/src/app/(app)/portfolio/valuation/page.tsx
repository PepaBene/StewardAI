import type { Metadata } from "next";
import { Suspense } from "react";

import { PageHeader } from "@/components/page-header";

import { PortfolioValuationView } from "./valuation-view";

export const metadata: Metadata = { title: "Valuation" };

export default function PortfolioValuationPage() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Portfolio"
        title="Valuation"
        description="What your holdings are worth on a fair-value basis you choose, against what the market prices them at today."
      />
      <Suspense>
        <PortfolioValuationView />
      </Suspense>
    </div>
  );
}
