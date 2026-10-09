import type { Metadata } from "next";
import { Suspense } from "react";

import { PartiesPageView } from "./parties-view";

export const metadata: Metadata = { title: "Deal parties to review" };

export default function DealPartiesPage() {
  return (
    <Suspense>
      <PartiesPageView />
    </Suspense>
  );
}
