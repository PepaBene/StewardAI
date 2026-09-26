import type { Metadata } from "next";
import { Suspense } from "react";

import { ResearchListView } from "./research-list-view";

export const metadata: Metadata = { title: "Research" };

export default function ResearchPage() {
  return (
    <Suspense>
      <ResearchListView />
    </Suspense>
  );
}
