import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { EvidenceView } from "./evidence-view";

export const metadata: Metadata = { title: "Evidence" };

export default async function EvidencePage({ params }: PageProps<"/evidence/[factId]">) {
  const factId = Number((await params).factId);
  if (!Number.isInteger(factId) || factId <= 0) notFound();
  return <EvidenceView factId={factId} />;
}
