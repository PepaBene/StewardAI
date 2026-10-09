import type { Metadata } from "next";
import { Suspense } from "react";

import { CoveragePageView } from "./coverage-view";

export const metadata: Metadata = { title: "Data coverage" };

export default function CoveragePage() {
  return (
    <Suspense>
      <CoveragePageView />
    </Suspense>
  );
}
