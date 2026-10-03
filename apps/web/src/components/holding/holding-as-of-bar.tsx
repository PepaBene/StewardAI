"use client";

import { AlertMessage } from "@/components/alert-message";
import { AsOfControl } from "@/components/network/as-of-control";
import { Blueprint } from "@/components/ui/blueprint";
import { isFutureDay } from "@/lib/holding-data/logic";

import { day } from "./shared";

const SECTIONS = [
  ["financial-history", "Financial history"],
  ["peers", "Peers"],
  ["exposure", "Exposure"],
  ["network", "Network and patents"],
  ["source-disagreements", "Source disagreements"],
  ["price-sources", "Price sources"],
  ["gaps", "Gaps"],
  ["ownership-control", "Ownership and control"],
  ["evidence", "Evidence"],
] as const;

/**
 * The one as-of control of the holding's data-context and evidence sections. Records the app
 * learned of after the chosen day are hidden by the backend; the stamp shows the cutoff the
 * response says it applied (`known_as_of`).
 */
export function HoldingAsOfBar({ value, onChange, today, knownAsOf }: { value: string; onChange: (v: string) => void; today: string; knownAsOf: string | null }) {
  const future = isFutureDay(value, today);
  return (
    <Blueprint id="known-as-of" aria-label="Known as of" className="flex min-w-0 scroll-mt-4 flex-col gap-2 px-5 py-4">
      <div className="kicker">Known as of</div>
      <AsOfControl value={value} onChange={onChange} today={today} />
      <p className="num text-xs text-text-muted">
        {knownAsOf ? `Cutoff applied by the API: ${day(knownAsOf)}. Records learned later are hidden.` : "Waiting for the API to state the cutoff it applied."}
        {value ? " This applies to the data-context and evidence sections below, not to prices or the thesis." : ""}
      </p>
      <nav aria-label="Data sections" className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {SECTIONS.map(([id, label]) => (
          <a key={id} href={`#${id}`} className="text-accent-ink hover:underline">
            {label}
          </a>
        ))}
      </nav>
      {future ? (
        <AlertMessage tone="info" title="Date is in the future">
          The API refuses an as-of date after today, so nothing is requested for it.
        </AlertMessage>
      ) : null}
    </Blueprint>
  );
}
