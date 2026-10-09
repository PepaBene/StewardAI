import type { Metadata } from "next";
import { Suspense } from "react";

import { NetworkPageView } from "./network-view";

export const metadata: Metadata = { title: "Customers and suppliers" };

export default function NetworkPage() {
  return (
    <Suspense>
      <NetworkPageView />
    </Suspense>
  );
}
