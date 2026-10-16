import { Suspense } from "react";

import { LotsView } from "../lots-view";

export default function LotsPage() {
  return (
    <Suspense>
      <LotsView />
    </Suspense>
  );
}

export const metadata = { title: "Lots and holding periods · Performance" };
