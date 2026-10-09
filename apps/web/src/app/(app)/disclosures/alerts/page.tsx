import type { Metadata } from "next";
import { Suspense } from "react";

import { AlertsPageView } from "./alerts-view";

export const metadata: Metadata = { title: "Disclosure alerts" };

export default function AlertsPage() {
  return (
    <Suspense>
      <AlertsPageView />
    </Suspense>
  );
}
