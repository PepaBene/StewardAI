import type { Metadata } from "next";
import { Suspense } from "react";

import { XrayView } from "./xray-view";

export const metadata: Metadata = { title: "Funds and ownership" };

export default function XrayPage() {
  return (
    <Suspense>
      <XrayView />
    </Suspense>
  );
}
