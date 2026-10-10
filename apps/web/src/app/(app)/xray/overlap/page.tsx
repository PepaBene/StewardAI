import { Suspense } from "react";

import { OverlapView } from "./overlap-view";

export default function OverlapPage() {
  return (
    <Suspense>
      <OverlapView />
    </Suspense>
  );
}
