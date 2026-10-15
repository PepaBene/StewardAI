import type { Metadata } from "next";
import { Suspense } from "react";

import { AutopilotView } from "./autopilot-view";

export const metadata: Metadata = { title: "Autopilot" };

export default function AutopilotPage() {
  return (
    <Suspense>
      <AutopilotView />
    </Suspense>
  );
}
