import type { Metadata } from "next";
import { Suspense } from "react";

import { ReplayPageView } from "./replay-view";

export const metadata: Metadata = { title: "Decision replay" };

export default function ReplayPage() {
  return (
    <Suspense>
      <ReplayPageView />
    </Suspense>
  );
}
