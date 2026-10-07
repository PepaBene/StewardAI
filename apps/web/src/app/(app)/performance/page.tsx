import { Suspense } from "react";

import { ReturnsView } from "./returns-view";

export default function PerformancePage() {
  return (
    <Suspense>
      <ReturnsView />
    </Suspense>
  );
}
