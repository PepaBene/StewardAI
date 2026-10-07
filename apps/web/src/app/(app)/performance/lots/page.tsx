import { Suspense } from "react";

import { LotsView } from "../lots-view";

export default function LotsPage() {
  return (
    <Suspense>
      <LotsView />
    </Suspense>
  );
}
