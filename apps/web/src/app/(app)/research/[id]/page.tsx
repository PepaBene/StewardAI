import type { Metadata } from "next";

import { ResearchReportView } from "./research-report-view";

export const metadata: Metadata = { title: "Research report" };

export default async function ResearchReportPage({ params }: PageProps<"/research/[id]">) {
  const id = Number((await params).id);
  return <ResearchReportView id={Number.isInteger(id) && id > 0 ? id : 0} />;
}
