"use client";

import { useMemo } from "react";

import { HBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { titleCase } from "@/lib/parse-untyped";
import { parseShadow } from "@/lib/verification/verification";

import { Verdict } from "./gates-panel";

const fetchShadow = () => getBrowserApiClient().GET("/verification/shadow-report");

const UNKNOWN = <span className="text-text-muted">Not measured</span>;

function Ratio({ value, label }: { value: number | null; label: string }) {
  return value === null ? UNKNOWN : <HBar value={value} label={label} formatValue={(v) => formatPct(v, 0)} />;
}

/** The latest 30-day shadow-collection sign-off report: which providers earn their keep. */
export function ShadowPanel() {
  const { state } = useApiResource(fetchShadow);
  const data = dataOf(state);
  const report = useMemo(() => (data ? parseShadow(data) : null), [data]);
  const notGenerated = state.kind === "error" && state.status === 404 && !state.previous;

  return (
    <Panel
      id="shadow"
      title="Shadow report"
      description="The sign-off report from the 30-day shadow collection: for each free provider, how much it adds beyond other sources, how often it fails and how much of its quota it uses. Informational only; it changes nothing by itself."
    >
      {notGenerated ? (
        <EmptyState title="No shadow report has been generated yet">Run the shadow collection for a few days, then generate the report.</EmptyState>
      ) : (
        <ResourceNotice state={state} what="shadow report" />
      )}
      {report ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <Verdict ok={report.signOffReady} yes="Ready to sign off" no="Not ready to sign off" />
            <span className="num text-xs text-text-muted">
              {report.daysObserved ?? "Unknown"} of {report.windowDays ?? "Unknown"} days observed
              {report.generatedAt ? ` · generated ${report.generatedAt.slice(0, 10)}` : ""}
            </span>
          </div>
          {report.blockers.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-5 text-sm">
              {report.blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          ) : null}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[44rem] border-collapse text-sm">
              <caption className="sr-only">Providers in the shadow report</caption>
              <thead>
                <tr className="border-b border-border text-left text-[11px] text-text-muted uppercase">
                  <th scope="col" className="py-1.5 pr-3 font-medium">Provider</th>
                  <th scope="col" className="py-1.5 pr-3 font-medium">Verdict</th>
                  <th scope="col" className="w-[18%] py-1.5 pr-3 font-medium">Value added, of its observations</th>
                  <th scope="col" className="w-[18%] py-1.5 pr-3 font-medium">Failures, of fetches</th>
                  <th scope="col" className="w-[18%] py-1.5 font-medium">Quota pressure</th>
                </tr>
              </thead>
              <tbody>
                {report.providers.map((p) => (
                  <tr key={p.provider} className="border-b border-border-soft align-top">
                    <th scope="row" className="py-1.5 pr-3 text-left font-mono text-xs font-normal">{p.provider}</th>
                    <td className="py-1.5 pr-3 text-xs">
                      {p.recommendation ? titleCase(p.recommendation) : UNKNOWN}
                      {p.reasons.length > 0 ? <span className="block text-text-muted">{p.reasons.join("; ")}</span> : null}
                    </td>
                    <td className="py-1.5 pr-3"><Ratio value={p.valueAddedRatio} label={`${p.provider} value added`} /></td>
                    <td className="py-1.5 pr-3"><Ratio value={p.failureRate} label={`${p.provider} failure rate`} /></td>
                    <td className="py-1.5"><Ratio value={p.quotaPressure} label={`${p.provider} quota pressure`} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="space-y-0.5 text-xs text-text-muted">
            {report.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </Panel>
  );
}
