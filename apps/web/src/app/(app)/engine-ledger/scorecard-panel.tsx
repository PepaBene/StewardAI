"use client";

import { useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { Blueprint } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

type Card = components["schemas"]["AutonomyScorecard"];

function Row({
  label,
  value,
  target,
  met,
}: {
  label: string;
  value: string;
  target: string;
  met: boolean | null;
}) {
  return (
    <li className="flex items-baseline justify-between gap-4 border-b border-border-soft py-1.5 text-sm">
      <span>{label}</span>
      <span className="text-right">
        <span className={cn("font-medium", met === false && "text-loss")}>{value}</span>{" "}
        <span className="text-text-muted">
          {target}
          {met === null ? " · not enough yet" : met ? " · met" : " · not met"}
        </span>
      </span>
    </li>
  );
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);

/** Is the autonomy working? The plan's success metrics, measured from what the app stores. */
export function ScorecardPanel() {
  const [c, setC] = useState<Card | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getBrowserApiClient()
      .GET("/engine/scorecard")
      .then((r) => {
        if (r.data) setC(r.data);
        else setError(`Could not load the scorecard (HTTP ${r.response.status}).`);
      });
  }, []);

  return (
    <Blueprint>
      <div className="space-y-3 p-5">
        <h2 className="text-[15px] font-medium">Is the autonomy working?</h2>
        <p className="max-w-prose text-sm text-text-muted">
          The targets from the autonomy plan, measured over the last {c?.window_days ?? 28} days from
          the decision ledger and your open notices. Precision only counts once enough decisions have
          been reviewed.
        </p>
        {error ? <AlertMessage title="Not loaded">{error}</AlertMessage> : null}
        {c ? (
          <div className="max-w-3xl space-y-4">
            <ul>
              <Row
                label="Items that needed you, per week"
                value={c.items_per_week.toString()}
                target={`target under ${c.items_per_week_target}`}
                met={c.items_met}
              />
              <Row
                label="Decisions made and applied by the app"
                value={pct(c.automatic_share)}
                target={`target ${pct(c.automatic_share_target)} (${c.decisions_automatic} applied, ${c.decisions_shadow} shadow, ${c.decisions_escalated} sent to you)`}
                met={c.automatic_met ?? null}
              />
              <Row
                label="Oldest item waiting for you"
                value={c.oldest_open_days === null || c.oldest_open_days === undefined ? "none" : `${c.oldest_open_days} days`}
                target={`target under ${c.oldest_open_days_target} days; ${c.items_open_now} open now`}
                met={c.oldest_met ?? null}
              />
              <Row
                label="AI spend this month"
                value={`$${c.spend_month.toFixed(2)}`}
                target={c.spend_cap ? `cap $${c.spend_cap.toFixed(2)}` : "no cap set"}
                met={c.spend_met ?? null}
              />
              <Row
                label="Jobs that missed their schedule"
                value={c.late_jobs.toString()}
                target="target 0"
                met={c.late_jobs === 0}
              />
              <Row
                label="Keys waiting to be pasted"
                value={c.missing_keys.toString()}
                target="named in Needs you"
                met={null}
              />
            </ul>
            {c.precision.length ? (
              <div className="space-y-1">
                <p className="text-sm font-medium">Precision by kind (target {pct(c.precision_target)})</p>
                <ul>
                  {c.precision.map((p) => (
                    <Row
                      key={p.kind}
                      label={p.kind}
                      value={pct(p.precision)}
                      target={`${p.right} of ${p.reviewed} reviewed right`}
                      met={p.met ?? null}
                    />
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </Blueprint>
  );
}
