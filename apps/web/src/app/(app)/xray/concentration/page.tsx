import { Suspense } from "react";

import { ConcentrationView } from "./concentration-view";

export default function ConcentrationPage() {
  return (
    <Suspense>
      <ConcentrationView />
    </Suspense>
  );
}
