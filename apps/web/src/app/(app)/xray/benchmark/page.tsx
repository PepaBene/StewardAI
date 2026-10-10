import { Suspense } from "react";

import { BenchmarkView } from "./benchmark-view";

export default function BenchmarkPage() {
  return (
    <Suspense>
      <BenchmarkView />
    </Suspense>
  );
}
