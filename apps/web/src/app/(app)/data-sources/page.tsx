import type { Metadata } from "next";
import { Suspense } from "react";

import { DataSourcesView } from "./data-sources-view";

export const metadata: Metadata = { title: "Data sources" };

export default function DataSourcesPage() {
  return (
    <Suspense>
      <DataSourcesView />
    </Suspense>
  );
}
