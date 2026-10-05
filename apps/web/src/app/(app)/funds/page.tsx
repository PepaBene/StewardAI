import type { Metadata } from "next";
import { Suspense } from "react";

import { FundsView } from "./funds-view";

export const metadata: Metadata = { title: "Funds" };

export default function FundsPage() {
  return (
    <Suspense>
      <FundsView />
    </Suspense>
  );
}
