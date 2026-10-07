import { Suspense } from "react";

import { ProposedView } from "./proposed-view";

export default function ProposedPage() {
  return (
    <Suspense>
      <ProposedView />
    </Suspense>
  );
}
