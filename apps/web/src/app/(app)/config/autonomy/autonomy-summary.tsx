"use client";

import { useEffect, useState } from "react";

import { StatTile, SummaryPanel } from "@/components/config/summary";
import { getBrowserApiClient } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

type Gates = components["schemas"]["GateOut"];
type Watchers = components["schemas"]["WatcherSettings"];

const percent = (fraction: number) => `${Math.round(fraction * 1000) / 10}%`;
const days = (n: number) => `${n} day${n === 1 ? "" : "s"}`;

/** What a change to the wrong-decision share means, in words. */
function wrongShareInWords(change: number): string {
  if (change === 0) return "no more wrong decisions";
  const points = Math.round(Math.abs(change) * 1000) / 10;
  return change < 0 ? `${points} points fewer wrong decisions` : `up to ${points} points more wrong decisions`;
}

/**
 * The current autonomy thresholds in words, read from the engine. Shown when the page loads;
 * the editing panels below save separately, so reload to see a saved change here.
 */
export function AutonomySummary() {
  const [gates, setGates] = useState<Gates | null>(null);
  const [watchers, setWatchers] = useState<Watchers | null>(null);

  useEffect(() => {
    const api = getBrowserApiClient();
    let cancelled = false;
    void api
      .GET("/engine/gates")
      .then((r) => {
        if (!cancelled && r.data) setGates(r.data);
      })
      .catch(() => undefined);
    void api
      .GET("/engine/watchers")
      .then((r) => {
        if (!cancelled && r.data) setWatchers(r.data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-4">
      <SummaryPanel
        title="Before a stricter floor is raised"
        reading={
          gates
            ? `The engine replays at least ${gates.min_n} past decisions, ${gates.min_reviewed} of them checked, and needs ${wrongShareInWords(gates.max_wrong_rate_increase)}.`
            : "Loading the current bar…"
        }
      >
        {gates ? (
          <div className="flex flex-wrap gap-2">
            <StatTile label="Decisions replayed" value={gates.min_n} note="at least" />
            <StatTile label="Of them, checked" value={gates.min_reviewed} note="by you or a second model" />
            <StatTile
              label="Wrong share must change by"
              value={percent(gates.max_wrong_rate_increase)}
              note={wrongShareInWords(gates.max_wrong_rate_increase)}
            />
          </div>
        ) : null}
      </SummaryPanel>
      <SummaryPanel
        title="Unprompted heads-ups"
        reading={
          watchers
            ? `A note for a ${percent(watchers.large_move)} one-day move, ${watchers.insider_min_people} insiders buying within ${days(watchers.insider_days)}, a review reminder ${days(watchers.review_lead_days)} ahead, and no repeat for ${days(watchers.cooldown_days)}.`
            : "Loading the current sensitivity…"
        }
      >
        {watchers ? (
          <div className="flex flex-wrap gap-2">
            <StatTile label="Price move worth a note" value={percent(watchers.large_move)} note="in one day" />
            <StatTile label="Insiders buying together" value={watchers.insider_min_people} note={`within ${days(watchers.insider_days)}`} />
            <StatTile label="Review notice" value={days(watchers.review_lead_days)} note="before month end" />
            <StatTile label="No repeat for" value={days(watchers.cooldown_days)} note="the same heads-up" />
          </div>
        ) : null}
      </SummaryPanel>
    </div>
  );
}
