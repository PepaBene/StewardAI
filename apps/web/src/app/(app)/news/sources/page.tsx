import type { Metadata } from "next";

import { SourcesView } from "./sources-view";

export const metadata: Metadata = { title: "News sources" };

export default function NewsSourcesPage() {
  return <SourcesView />;
}
