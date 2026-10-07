import { Suspense } from "react";

import { LedgerView } from "../ledger-view";

export default function LedgerPage() {
  return (
    <Suspense>
      <LedgerView />
    </Suspense>
  );
}
