import type { Metadata } from "next";
import { Suspense } from "react";

import { EscalationsView } from "./escalations-view";

export const metadata: Metadata = { title: "Impact notes" };

export default function EscalationsPage() {
  return (
    <Suspense>
      <EscalationsView />
    </Suspense>
  );
}
