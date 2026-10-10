import type { Metadata } from "next";
import { Suspense } from "react";

import { CompareView } from "./compare-view";

export const metadata: Metadata = { title: "Compare funds" };

export default function CompareFundsPage() {
  return (
    <Suspense>
      <CompareView />
    </Suspense>
  );
}
