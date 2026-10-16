import type { Metadata } from "next";
import { Suspense } from "react";

import { PageHeader } from "@/components/page-header";

import { IncomeView } from "./income-view";

export const metadata: Metadata = { title: "Income" };

export default function IncomePage() {
  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        kicker="Portfolio"
        title="Income"
        description="Dividends you received, what is declared and what the payment pattern suggests for the next twelve months."
      />
      <Suspense>
        <IncomeView />
      </Suspense>
    </div>
  );
}
