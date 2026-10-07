import type { Metadata } from "next";
import { Suspense } from "react";

import { LookThroughPageView } from "./look-through-view";

export const metadata: Metadata = { title: "Portfolio look-through" };

export default function LookThroughPage() {
  return (
    <Suspense>
      <LookThroughPageView />
    </Suspense>
  );
}
