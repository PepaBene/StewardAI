import type { Metadata } from "next";
import { Suspense } from "react";

import { VerificationView } from "./verification-view";

export const metadata: Metadata = { title: "Verification" };

export default function VerificationPage() {
  return (
    <Suspense>
      <VerificationView />
    </Suspense>
  );
}
