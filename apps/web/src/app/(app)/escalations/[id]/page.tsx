import type { Metadata } from "next";

import { EscalationView } from "./escalation-view";

export const metadata: Metadata = { title: "Impact note" };

export default async function EscalationPage({ params }: PageProps<"/escalations/[id]">) {
  const id = Number((await params).id);
  return <EscalationView id={Number.isInteger(id) && id > 0 ? id : 0} />;
}
