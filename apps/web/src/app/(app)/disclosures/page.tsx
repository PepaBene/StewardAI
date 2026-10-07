import type { Metadata } from "next";
import { Suspense } from "react";

import { DisclosuresView } from "./disclosures-view";

export const metadata: Metadata = { title: "Public disclosures" };

export default function DisclosuresPage() {
  return (
    <Suspense>
      <DisclosuresView />
    </Suspense>
  );
}
