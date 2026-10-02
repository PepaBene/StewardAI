import type { Metadata } from "next";
import { Suspense } from "react";

import { FinancialsView } from "./financials-view";

export const metadata: Metadata = { title: "Financials" };

export default function FinancialsPage() {
  return (
    <Suspense>
      <FinancialsView />
    </Suspense>
  );
}
