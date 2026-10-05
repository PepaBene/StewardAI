"use client";

import { useMemo } from "react";

import { HBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { parseSignals, signalLabel } from "@/lib/verification/verification";

import { Verdict } from "./gates-panel";

const fetchSignals = () => getBrowserApiClient().GET("/verification/signals");

/**
 * One card per signal: may it be published? The API returns the latest audit only, so there is
 * no history to chart; precision is drawn as a meter against the audit's own sample.
 */
export function SignalsPanel() {
  const { state } = useApiResource(fetchSignals);
  const data = dataOf(state);
  const signals = useMemo(() => (data ? parseSignals(data) : []), [data]);

  return (
    <Panel
      id="signals"
      title="Signal publish checks"
      description="A signal may be published only after a reviewed audit shows its evidence is accurate and that no value leaked in from after the date it describes. Precision is the share of audited evidence rows that were correct."
    >
      <ResourceNotice state={state} what="signal checks" />
      {data && signals.length === 0 ? <EmptyState title="No signals are defined">There is nothing to audit yet.</EmptyState> : null}
      {signals.length > 0 ? (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {signals.map((s) => (
            <li key={s.name} className="space-y-2 border border-border-soft px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-semibold">{signalLabel(s.name)}</h3>
                <Verdict ok={s.publishable} yes="Publishable" no="Not publishable" />
              </div>
              <HBar
                value={s.precision}
                label={`${signalLabel(s.name)} evidence precision`}
                formatValue={(v) => `${formatPct(v, 0)} correct`}
              />
              <p className="num text-xs text-text-muted">
                {s.precision === null ? "Precision not measured" : `Precision of ${s.sampleSize ?? "an unknown number of"} audited rows`}
                {" · "}
                {s.pitChecked === null ? "point-in-time not checked" : `${s.pitViolations ?? "unknown"} point-in-time violations in ${s.pitChecked} checked`}
              </p>
              <p className="text-xs text-text-muted">
                {s.auditedAt ? `Audited ${s.auditedAt.slice(0, 10)}${s.reviewer ? ` by ${s.reviewer}` : ""}` : "Never audited"}
              </p>
              {s.reasons.length > 0 ? (
                <ul className="space-y-0.5 text-xs">
                  {s.reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
