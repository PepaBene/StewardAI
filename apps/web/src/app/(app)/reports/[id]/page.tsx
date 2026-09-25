import type { Metadata } from "next";

import { DigestView } from "./digest-view";

export const metadata: Metadata = { title: "Digest" };

export default async function ReportPage({ params }: PageProps<"/reports/[id]">) {
  const id = Number((await params).id);
  return <DigestView id={Number.isInteger(id) && id > 0 ? id : 0} />;
}
