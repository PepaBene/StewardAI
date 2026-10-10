import { Suspense } from "react";

import { AttributionView } from "../attribution-view";

export default function AttributionPage() {
  return (
    <Suspense>
      <AttributionView />
    </Suspense>
  );
}
