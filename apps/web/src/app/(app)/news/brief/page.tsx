import type { Metadata } from "next";
import { Suspense } from "react";

import { BriefView } from "./brief-view";

export const metadata: Metadata = { title: "News brief" };

export default function BriefPage() {
  return (
    <Suspense>
      <BriefView />
    </Suspense>
  );
}
