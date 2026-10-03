import type { Metadata } from "next";
import { Suspense } from "react";

import { CoverageAtlas } from "@/components/coverage/atlas";

export const metadata: Metadata = { title: "Coverage atlas" };

export default function CoveragePage() {
  return (
    <Suspense>
      <CoverageAtlas />
    </Suspense>
  );
}
