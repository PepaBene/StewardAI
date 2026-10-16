import { Suspense } from "react";

import { SleevesView } from "../sleeves-view";

export default function CoreVsStocksPage() {
  return (
    <Suspense>
      <SleevesView />
    </Suspense>
  );
}

export const metadata = { title: "Core and stocks · Performance" };
