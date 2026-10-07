import type { Metadata } from "next";
import { Suspense } from "react";

import { ScreenPageView } from "./screen-view";

export const metadata: Metadata = { title: "Evidence screen" };

export default function ScreenPage() {
  return (
    <Suspense>
      <ScreenPageView />
    </Suspense>
  );
}
