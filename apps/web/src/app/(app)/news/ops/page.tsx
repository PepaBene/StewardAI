import type { Metadata } from "next";

import { OpsView } from "./ops-view";

export const metadata: Metadata = { title: "News review" };

export default function NewsOpsPage() {
  return <OpsView />;
}
