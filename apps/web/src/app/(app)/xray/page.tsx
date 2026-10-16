import { Suspense } from "react";

import { ExposureView } from "./exposure-view";

export default function XrayPage() {
  return (
    <Suspense>
      <ExposureView />
    </Suspense>
  );
}

export const metadata = { title: "Exposure · X-Ray" };
