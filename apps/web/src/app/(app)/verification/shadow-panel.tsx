"use client";

import { useMemo } from "react";

import { HBar } from "@/components/charts";
import { EmptyState } from "@/components/empty-state";
import { dataOf, Panel, ResourceNotice } from "@/components/oversight/panel";
import { CompactRow } from "@/components/ui/compact-row";
import { ResponsiveTable } from "@/components/ui/responsive-table";
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
      rounded
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
              {report.generatedAt ? ` · made on ${report.generatedAt.slice(0, 10)}` : ""}
            </span>
          </div>
          {report.blockers.length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-5 text-sm">
              {report.blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          ) : null}
          <ResponsiveTable
            rows={report.providers}
            rowKey={(p) => p.provider}
            caption="Providers in the shadow report"
            columns={[
              { key: "provider", header: "Provider", cell: (p) => <span className="font-medium">{titleCase(p.provider)}</span> },
              {
                key: "verdict",
                header: "Verdict",
                className: "text-xs",
                cell: (p) => (
                  <>
                    {p.recommendation ? titleCase(p.recommendation) : UNKNOWN}
                    {p.reasons.length > 0 ? <span className="block text-text-muted">{p.reasons.join("; ")}</span> : null}
                  </>
                ),
              },
              { key: "value", header: "Adds beyond other sources", className: "w-[18%]", cell: (p) => <Ratio value={p.valueAddedRatio} label={`${titleCase(p.provider)} adds beyond other sources`} /> },
              { key: "failures", header: "Fetches that failed", className: "w-[18%]", cell: (p) => <Ratio value={p.failureRate} label={`${titleCase(p.provider)} fetches that failed`} /> },
              { key: "quota", header: "Quota used", className: "w-[18%]", cell: (p) => <Ratio value={p.quotaPressure} label={`${titleCase(p.provider)} quota used`} /> },
            ]}
            compact={(p) => (
              <CompactRow
                name={titleCase(p.provider)}
                symbol={p.recommendation ? titleCase(p.recommendation) : "Not measured"}
                secondary={[
                  `Adds ${p.valueAddedRatio === null ? "not measured" : formatPct(p.valueAddedRatio, 0)}`,
                  `Failed ${p.failureRate === null ? "not measured" : formatPct(p.failureRate, 0)}`,
                ]}
              />
            )}
            empty={<p className="text-sm text-text-muted">The report lists no providers.</p>}
          />
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
