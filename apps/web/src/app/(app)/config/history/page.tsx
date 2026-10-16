import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";

import { ConfigTransfer } from "../config-transfer";
import { HistoryTimelines } from "./history-timelines";

export const metadata: Metadata = { title: "Config history" };

export default function ConfigHistoryIndex() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Version history"
        description="Open an area to compare any two saved versions and roll back. Export or import every area at once below."
      />
      <HistoryTimelines />
      <ConfigTransfer />
    </div>
  );
}
