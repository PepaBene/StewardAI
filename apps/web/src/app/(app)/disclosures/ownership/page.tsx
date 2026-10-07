import type { Metadata } from "next";
import { Suspense } from "react";

import { OwnershipPageView } from "./ownership-view";

export const metadata: Metadata = { title: "Ownership" };

export default function OwnershipPage() {
  return (
    <Suspense>
      <OwnershipPageView />
    </Suspense>
  );
}
