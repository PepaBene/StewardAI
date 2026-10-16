import { Suspense } from "react";

import { FundamentalsView } from "./fundamentals-view";

export default function FundamentalsPage() {
  return (
    <Suspense>
      <FundamentalsView />
    </Suspense>
  );
}

export const metadata = { title: "Fundamentals · X-Ray" };
