import type { Metadata } from "next";

import { ResearchView } from "./research-view";

export const metadata: Metadata = { title: "Fund research" };

export default function FundResearchPage() {
  return <ResearchView />;
}
