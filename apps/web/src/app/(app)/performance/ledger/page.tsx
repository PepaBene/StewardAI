import { Suspense } from "react";

import { LedgerView } from "../ledger-view";

export default function LedgerPage() {
  return (
    <Suspense>
      <LedgerView />
    </Suspense>
  );
}

export const metadata = { title: "Ledger and settings · Performance" };
