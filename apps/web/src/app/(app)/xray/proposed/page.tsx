import { Suspense } from "react";

import { ProposedView } from "./proposed-view";

export default function ProposedPage() {
  return (
    <Suspense>
      <ProposedView />
    </Suspense>
  );
}

export const metadata = { title: "What if · X-Ray" };
