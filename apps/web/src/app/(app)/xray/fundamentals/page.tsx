import { Suspense } from "react";

import { FundamentalsView } from "./fundamentals-view";

export default function FundamentalsPage() {
  return (
    <Suspense>
      <FundamentalsView />
    </Suspense>
  );
}
