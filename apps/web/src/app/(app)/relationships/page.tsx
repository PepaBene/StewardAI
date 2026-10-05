import type { Metadata } from "next";
import { Suspense } from "react";

import { RelationshipsView } from "./relationships-view";

export const metadata: Metadata = { title: "Relationship review" };

export default function RelationshipsPage() {
  return (
    <Suspense>
      <RelationshipsView />
    </Suspense>
  );
}
