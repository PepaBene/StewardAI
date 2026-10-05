"use client";

import { useCallback, useMemo, useState } from "react";

import { AuditTrail, HBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { parseHistory, TREND_TEXT } from "@/lib/verification/history";
import { parseSignals, signalLabel } from "@/lib/verification/verification";

import { Verdict } from "./gates-panel";

const fetchSignals = () => getBrowserApiClient().GET("/verification/signals");

/**
 * One card per signal: may it be published? The meter shows the latest audit; opening "Audit
 * history" loads every recorded audit for that signal and plots precision by date.
 */
function SignalHistory({ name }: { name: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="text-sm" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="cursor-pointer text-xs text-accent-ink hover:underline">Audit history</summary>
      {open ? <HistoryBody name={name} /> : null}
    </details>
  );
}

function HistoryBody({ name }: { name: string }) {
  const fetcher = useCallback(
    () => getBrowserApiClient().GET("/verification/signals/{signal_name}/history", { params: { path: { signal_name: name }, query: {} } }),
    [name],
  );
  const { state } = useApiResource(fetcher);
  const data = dataOf(state);
  const history = useMemo(() => (data ? parseHistory(data) : null), [data]);
  return (
    <div className="mt-2 space-y-2">
      <ResourceNotice state={state} what="audit history" />
      {history && history.audits.length === 0 ? <p className="text-xs text-text-muted">This signal has never been audited, so there is nothing to chart.</p> : null}
      {history && history.audits.length > 0 ? (
        <>
          <AuditTrail
            label={`${signalLabel(name)}: evidence precision by audit`}
            points={history.audits.map((a) => ({ at: a.at, value: a.precision, sample: a.sample }))}
          />
          <p className="text-xs text-text-muted">
            {TREND_TEXT[history.trend]}. Share of audited evidence rows that were correct, by audit date; a larger square means a larger sample.
          </p>
          <table className="w-full text-xs">
            <caption className="sr-only">Point-in-time checks by audit</caption>
            <thead>
              <tr className="text-left text-text-muted">
                <th className="py-1 font-normal">Audited</th>
                <th className="py-1 text-right font-normal">Sample</th>
                <th className="py-1 text-right font-normal">Point-in-time violations</th>
                <th className="py-1 font-normal">Reviewer</th>
              </tr>
            </thead>
            <tbody>
              {[...history.audits].reverse().map((a) => (
                <tr key={`${a.id}-${a.at}`} className="border-t border-border-soft">
                  <td className="num py-1">{a.at.slice(0, 10)}</td>
                  <td className="num py-1 text-right">{a.sample ?? "Unknown"}</td>
                  <td className="num py-1 text-right">{a.pitChecked === null ? "Not checked" : `${a.pitViolations ?? "Unknown"} of ${a.pitChecked}`}</td>
                  <td className="py-1">{a.reviewer ?? "Not named"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </div>
  );
}

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
              <SignalHistory name={s.name} />
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}
