import { Suspense } from "react";

import { SleevesView } from "../sleeves-view";

export default function CoreVsStocksPage() {
  return (
    <Suspense>
      <SleevesView />
    </Suspense>
  );
}
