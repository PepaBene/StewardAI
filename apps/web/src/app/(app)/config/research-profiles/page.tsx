import type { Metadata } from "next";

import { ResearchProfilesEditor } from "./research-profiles-editor";

export const metadata: Metadata = { title: "Research profiles" };

export default function ResearchProfilesPage() {
  return <ResearchProfilesEditor />;
}
