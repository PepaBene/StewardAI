import type { Metadata } from "next";
import { Suspense } from "react";

import { MarketsView } from "./markets-view";

export const metadata: Metadata = { title: "Price coverage" };

export default function MarketsPage() {
  return (
    <Suspense>
      <MarketsView />
    </Suspense>
  );
}
