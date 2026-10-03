import type { Metadata } from "next";
import { Suspense } from "react";

import { ScreenBuilder } from "@/components/screens/screen-builder";

export const metadata: Metadata = { title: "Screens" };

export default function ScreensPage() {
  return (
    <Suspense>
      <ScreenBuilder />
    </Suspense>
  );
}
