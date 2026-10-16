import { Suspense } from "react";

import { SupplyChainView } from "./supply-chain-view";

export default function SupplyChainPage() {
  return (
    <Suspense>
      <SupplyChainView />
    </Suspense>
  );
}

export const metadata = { title: "Supply chain · X-Ray" };
