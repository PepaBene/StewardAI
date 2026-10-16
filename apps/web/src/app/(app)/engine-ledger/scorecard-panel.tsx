"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { KpiStrip, type KpiTileProps } from "@/components/insight/kpi-tile";
import { BulletRow } from "@/components/oversight/bullet";
import { StatusList } from "@/components/oversight/status-list";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { gateRule, precisionRows } from "@/lib/oversight/gates";

type Card = components["schemas"]["AutonomyScorecard"];

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
const tone = (met: boolean | null | undefined): "good" | "caution" | "unknown" => (met === null || met === undefined ? "unknown" : met ? "good" : "caution");

function strip(c: Card): KpiTileProps[] {
  return [
    {
      label: "Items that needed you, per week",
      value: c.items_per_week,
      verdict: { tone: tone(c.items_met), text: `Target under ${c.items_per_week_target} a week.` },
    },
    {
      label: "Decided by the app",
      value: pct(c.automatic_share),
      verdict: { tone: tone(c.automatic_met), text: `Target ${pct(c.automatic_share_target)}; ${c.decisions_automatic} applied, ${c.decisions_escalated} sent to you.` },
    },
    {
      label: "Oldest item waiting",
      value: c.oldest_open_days === null || c.oldest_open_days === undefined ? "None" : `${c.oldest_open_days} d`,
      verdict: { tone: tone(c.oldest_met), text: `Target under ${c.oldest_open_days_target} days; ${c.items_open_now} open now.` },
    },
    {
      label: "Jobs that ran late",
      value: c.late_jobs,
      verdict: { tone: c.late_jobs === 0 ? "good" : "caution", text: c.late_jobs === 0 ? "Every job ran on schedule." : "See Activity for which." },
    },
  ];
}

/** Is the autonomy working? Each target as a bullet chart: actual against target, met or not. */
export function ScorecardPanel() {
  const [c, setC] = useState<Card | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void getBrowserApiClient()
      .GET("/engine/scorecard")
      .then((r) => {
        if (r.data) setC(r.data);
        else setError("The scorecard could not be loaded. Reload the page; if it keeps failing, check Status.");
      });
  }, []);

  return (
    <div className="flex flex-col gap-5">
      {c ? <KpiStrip tiles={strip(c)} label="Autonomy at a glance" /> : null}
      <Blueprint>
        <div className="space-y-3 px-[22px] py-5">
          <div>
            <h2 className="panel-title">Is the autonomy working?</h2>
            <p className="max-w-prose text-sm text-text-muted">
              Each bar is the actual figure over the last {c?.window_days ?? 28} days; the tick is the target. Precision only counts once enough
              decisions have been reviewed. Targets are set in{" "}
              <Link href="/config/autonomy" className="text-accent-ink underline underline-offset-2">
                Settings
              </Link>
              .
            </p>
          </div>
          {error ? <AlertMessage title="Not loaded">{error}</AlertMessage> : null}
          {c ? (
            <div className="space-y-4">
              <ul aria-label="Autonomy targets">
                <BulletRow
                  label="Items that needed you, per week"
                  actual={c.items_per_week}
                  target={c.items_per_week_target}
                  better="lower"
                  met={c.items_met}
                  actualText={String(c.items_per_week)}
                  targetText={String(c.items_per_week_target)}
                />
                <BulletRow
                  label="Decisions made and applied by the app"
                  actual={c.automatic_share ?? null}
                  target={c.automatic_share_target}
                  max={1}
                  met={c.automatic_met ?? null}
                  actualText={pct(c.automatic_share)}
                  targetText={pct(c.automatic_share_target)}
                  note={`${c.decisions_shadow} shadow`}
                />
                <BulletRow
                  label="Oldest item waiting for you"
                  actual={c.oldest_open_days ?? 0}
                  target={c.oldest_open_days_target}
                  better="lower"
                  met={c.oldest_met ?? null}
                  actualText={c.oldest_open_days === null || c.oldest_open_days === undefined ? "none" : `${c.oldest_open_days} days`}
                  targetText={`${c.oldest_open_days_target} days`}
                />
                <BulletRow
                  label="AI spend this month"
                  actual={c.spend_month}
                  target={c.spend_cap ?? null}
                  better="lower"
                  met={c.spend_met ?? null}
                  actualText={`$${c.spend_month.toFixed(2)}`}
                  targetText={c.spend_cap ? `$${c.spend_cap.toFixed(2)} cap` : "no cap set"}
                />
                <BulletRow
                  label="Jobs that missed their schedule"
                  actual={c.late_jobs}
                  target={0}
                  better="lower"
                  max={Math.max(c.late_jobs, 3)}
                  met={c.late_jobs === 0}
                  actualText={String(c.late_jobs)}
                  targetText="0"
                />
              </ul>
              {c.missing_keys > 0 ? (
                <p className="text-sm text-text-muted">
                  {c.missing_keys} key{c.missing_keys === 1 ? " is" : "s are"} waiting to be pasted; they are named under Needs you.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      </Blueprint>
      {c ? <GuardrailsPanel card={c} /> : null}
    </div>
  );
}

/**
 * The guardrails as check rows: the floor gate in one sentence, then each decision kind's
 * reviewed precision against the target, unmeasured until the gate's reviewed minimum is met.
 */
function GuardrailsPanel({ card }: { card: Card }) {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/engine/gates"), []);
  const { state } = useApiResource(fetcher);
  const gates = state.kind === "ok" ? state.data : null;
  const rows = precisionRows(card.precision, card.precision_target, gates);
  return (
    <Blueprint aria-labelledby="guardrails-heading" className="space-y-3 px-[22px] py-5">
      <PanelHeading
        title={<span id="guardrails-heading">Guardrails</span>}
        kicker="What the engine must prove before it acts more on its own"
        action={
          <Link href="/config/autonomy" className="text-sm text-accent-ink hover:underline">
            Change the bar
          </Link>
        }
      />
      {gates ? <p className="max-w-prose text-sm text-text-muted">{gateRule(gates)}</p> : null}
      {rows.length ? (
        <StatusList rows={rows} caption="Precision by kind of decision" passWord="clear the target" />
      ) : (
        <p className="text-sm text-text-muted">No decision has been reviewed yet, so no kind can be judged.</p>
      )}
    </Blueprint>
  );
}
