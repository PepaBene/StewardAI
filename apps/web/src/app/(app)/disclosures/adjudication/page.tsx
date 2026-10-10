import type { Metadata } from "next";
import { Suspense } from "react";

import { AdjudicationPageView } from "./adjudication-view";

export const metadata: Metadata = { title: "Accuracy checks" };

export default function AdjudicationPage() {
  return (
    <Suspense>
      <AdjudicationPageView />
    </Suspense>
  );
}
