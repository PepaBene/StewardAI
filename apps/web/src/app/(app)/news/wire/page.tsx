import type { Metadata } from "next";
import { Suspense } from "react";

import { WireView } from "./wire-view";

export const metadata: Metadata = { title: "News wire" };

export default function WirePage() {
  return (
    <Suspense>
      <WireView />
    </Suspense>
  );
}
