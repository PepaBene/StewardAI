import type { Metadata } from "next";
import { Suspense } from "react";

import { ScreensView } from "./screens-view";

export const metadata: Metadata = { title: "Screens" };

export default function ScreensPage() {
  return (
    <Suspense>
      <ScreensView />
    </Suspense>
  );
}
