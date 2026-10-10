import { Suspense } from "react";

import { SupplyChainView } from "./supply-chain-view";

export default function SupplyChainPage() {
  return (
    <Suspense>
      <SupplyChainView />
    </Suspense>
  );
}
