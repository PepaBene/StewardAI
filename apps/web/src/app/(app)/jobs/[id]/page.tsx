import type { Metadata } from "next";

import { JobView } from "./job-view";

export const metadata: Metadata = { title: "Job" };

export default async function JobPage({ params }: PageProps<"/jobs/[id]">) {
  const id = Number((await params).id);
  return <JobView id={Number.isInteger(id) && id > 0 ? id : 0} />;
}
