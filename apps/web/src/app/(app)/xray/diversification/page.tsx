import { Suspense } from "react";

import { DiversificationView } from "./diversification-view";

export default function DiversificationPage() {
  return (
    <Suspense>
      <DiversificationView />
    </Suspense>
  );
}

export const metadata = { title: "Diversification · X-Ray" };
