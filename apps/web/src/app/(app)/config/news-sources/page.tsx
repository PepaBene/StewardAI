import type { Metadata } from "next";

import { SourcesEditor } from "./sources-editor";

export const metadata: Metadata = { title: "News sources" };

export default function NewsSourcesPage() {
  return <SourcesEditor />;
}
