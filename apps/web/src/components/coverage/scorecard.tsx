import { CircleCheckIcon, CircleDashedIcon, CircleHelpIcon, CircleXIcon, ScrollTextIcon, type LucideIcon } from "lucide-react";

import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { ScoreItem, ScoreStatus } from "@/lib/coverage/types";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS: Record<ScoreStatus, { label: string; Icon: LucideIcon; cls: string }> = {
  met: { label: "Target met", Icon: CircleCheckIcon, cls: "border-verified/40 bg-verified-subtle" },
  not_met: { label: "Target not met", Icon: CircleXIcon, cls: "border-warning-signal/40 bg-warning-signal-subtle" },
  reported: { label: "Reported, no threshold", Icon: ScrollTextIcon, cls: "border-info-signal/40 bg-info-signal-subtle" },
  unavailable: { label: "Unavailable", Icon: CircleHelpIcon, cls: "border-border bg-neutral-signal-subtle" },
  not_measured: { label: "Not measured by the API", Icon: CircleDashedIcon, cls: "border-border bg-neutral-signal-subtle" },
};

/**
 * The plan's release targets, each beside the figure the API returned for it (or the reason none
 * exists). Nothing here is estimated: a target without an endpoint says so instead of a number.
 */
export function Scorecard({ items }: { items: readonly ScoreItem[] }) {
  return (
    <Blueprint as="section" aria-labelledby="scorecard-heading" id="scorecard" className="flex scroll-mt-24 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Plan release targets" title={<span id="scorecard-heading">Measured coverage scorecard</span>} />
      <p className="max-w-prose text-sm text-text">
        {items.filter((i) => i.status === "met").length} of {items.length} targets are met on API figures;{" "}
        {items.filter((i) => i.status === "not_measured").length} have no API figure yet. Targets come from the free-data plan; figures come only from the backend.
      </p>
      <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 md:grid-cols-2">
        {items.map((i) => {
          const s = STATUS[i.status];
          return (
            <li key={i.id} className="flex min-w-0 flex-col gap-2 border border-border-soft p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-base font-semibold">{i.title}</h3>
                <span className={cn("inline-flex items-center gap-1 border px-1.5 py-0.5 text-xs font-medium", s.cls)}>
                  <s.Icon aria-hidden="true" className="size-3.5" />
                  {s.label}
                </span>
              </div>
              <p className="text-xs text-text-muted">Target: {i.target}</p>
              {i.measured ? <p className="num text-text">{i.measured}</p> : null}
              {i.share !== null ? (
                <div role="meter" aria-label={`${i.title} share`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(i.share * 100)} className="h-1.5 w-full bg-border-soft">
                  <div className="h-full bg-accent" style={{ width: `${i.share * 100}%` }} />
                </div>
              ) : null}
              <p className="text-xs text-text-muted">{i.detail}</p>
              <p className="num text-[11px] text-text-faint">
                Source: {i.source}
                {i.asOf ? ` · as of ${formatDate(i.asOf.slice(0, 10))}` : ""}
              </p>
            </li>
          );
        })}
      </ul>
    </Blueprint>
  );
}
