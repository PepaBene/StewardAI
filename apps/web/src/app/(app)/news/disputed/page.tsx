import type { Metadata } from "next";
import { Suspense } from "react";

import { DisputedView } from "./disputed-view";

export const metadata: Metadata = { title: "Disputed stories" };

export default function DisputedPage() {
  return (
    <Suspense>
      <DisputedView />
    </Suspense>
  );
}
