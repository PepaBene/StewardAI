import type { Metadata } from "next";
import { Suspense } from "react";

import { AiDecisionsView } from "./ai-decisions-view";

export const metadata: Metadata = { title: "AI decisions" };

export default function AiDecisionsPage() {
  return (
    <Suspense>
      <AiDecisionsView />
    </Suspense>
  );
}
