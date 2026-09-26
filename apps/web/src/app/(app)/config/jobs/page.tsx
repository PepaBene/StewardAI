import type { Metadata } from "next";

import { JobsEditor } from "./jobs-editor";

export const metadata: Metadata = { title: "Jobs" };

export default function JobsPage() {
  return <JobsEditor />;
}
