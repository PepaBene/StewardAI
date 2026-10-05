import type { Metadata } from "next";
import { Suspense } from "react";

import { MacroView } from "./macro-view";

export const metadata: Metadata = { title: "Macro series" };

export default function MacroPage() {
  return (
    <Suspense>
      <MacroView />
    </Suspense>
  );
}
