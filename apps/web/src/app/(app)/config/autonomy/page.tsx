import type { Metadata } from "next";

import { MoreSettings } from "@/components/config/summary";
import { PageHeader } from "@/components/page-header";

import { GatesPanel, WatchersPanel } from "../../engine-ledger/gates-panel";
import { AutonomySummary } from "./autonomy-summary";

export const metadata: Metadata = { title: "Autonomy · Settings" };

/** Settings › Automation › Autonomy: the engine's thresholds, moved here from System › Autonomy. */
export default function AutonomySettingsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Autonomy"
        description="How sure the engine must be before it acts on its own, and how sensitive its unprompted heads-ups are."
      />
      <AutonomySummary />
      <MoreSettings title="Confidence floor bar" hint="What a stricter floor must prove first">
        <GatesPanel />
      </MoreSettings>
      <MoreSettings title="Heads-up sensitivity" hint="When the engine tells you without being asked">
        <WatchersPanel />
      </MoreSettings>
    </div>
  );
}
