import type { Metadata } from "next";
import { Suspense } from "react";

import { DealsPageView } from "./deals-view";

export const metadata: Metadata = { title: "Deals and financing" };

export default function DealsPage() {
  return (
    <Suspense>
      <DealsPageView />
    </Suspense>
  );
}
