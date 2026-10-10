import type { Metadata } from "next";
import { Suspense } from "react";

import { InsidersPageView } from "./insiders-view";

export const metadata: Metadata = { title: "Insider activity" };

export default function InsidersPage() {
  return (
    <Suspense>
      <InsidersPageView />
    </Suspense>
  );
}
