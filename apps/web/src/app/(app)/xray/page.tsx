import { Suspense } from "react";

import { ExposureView } from "./exposure-view";

export default function XrayPage() {
  return (
    <Suspense>
      <ExposureView />
    </Suspense>
  );
}
