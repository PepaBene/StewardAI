import type { Metadata } from "next";
import { Suspense } from "react";

import { LedgerView } from "./ledger-view";

export const metadata: Metadata = { title: "What the engine did" };

export default function EngineLedgerPage() {
  return (
    <Suspense>
      <LedgerView />
    </Suspense>
  );
}
