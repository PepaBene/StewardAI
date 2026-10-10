import type { Metadata } from "next";
import { Suspense } from "react";

import { EstimatesPageView } from "./estimates-view";

export const metadata: Metadata = { title: "Published estimates" };

export default function EstimatesPage() {
  return (
    <Suspense>
      <EstimatesPageView />
    </Suspense>
  );
}
