"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { KpiStrip, type KpiTileProps } from "@/components/insight/kpi-tile";
import { StatusList, type StatusRow } from "@/components/oversight/status-list";
import { Problem, problemText } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { CompactRow } from "@/components/ui/compact-row";
import { ResponsiveTable } from "@/components/ui/responsive-table";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { ISSUE_LABEL } from "@/lib/journal/journal";
import { titleCase } from "@/lib/parse-untyped";
import { pilotDayLabel, type PilotReport, type PilotState } from "@/lib/release/release";

import { DefectLog } from "./defect-log";

const fetchPilot = () => getBrowserApiClient().GET("/release/pilot");

export function PilotView() {
  const { state, reload } = useApiResource(fetchPilot);
  return (
    <ResourceGate state={state} label="the pilot">
      {(pilot) => <Body pilot={pilot} onStarted={reload} />}
    </ResourceGate>
  );
}

function Body({ pilot, onStarted }: { pilot: PilotState; onStarted: () => void }) {
  return (
    <div className="flex flex-col gap-5">
      <StartPanel pilot={pilot} onStarted={onStarted} />
      {pilot.report ? <Report report={pilot.report} /> : null}
      <details className="group rounded-card bg-surface-muted px-5 py-3">
        <summary className="cursor-pointer text-[15px] font-semibold">Details: defects you logged yourself</summary>
        <div className="mt-3">
          <DefectLog />
        </div>
      </details>
    </div>
  );
}

function StartPanel({ pilot, onStarted }: { pilot: PilotState; onStarted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const w = pilot.window;
  const running = w !== null && !w.over;

  async function start() {
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/release/pilot", { body: { length_days: 30 } });
    setBusy(false);
    if (!res.response.ok) return setProblem(problemText(res.error, "The pilot could not be started. Check that the API is running, then try again."));
    onStarted();
  }

  return (
    <Blueprint className="p-4">
      <PanelHeading level={2} title={w === null ? "No pilot has started" : pilotDayLabel(w.day, w.length_days, w.over)} />
      <p className="mt-2 max-w-prose text-sm">
        {w === null
          ? "While a pilot runs, the app counts which pages you open, how many alerts you rate as wrong, how often sources fail and how much of the portfolio the data checks flag. Nothing is sent anywhere."
          : `From ${formatDate(w.started_on)} to ${formatDate(w.ends_on)}.${w.over ? " The window is closed; page use is no longer counted." : ""}`}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button type="button" onClick={start} disabled={busy || running} variant={w === null || w.over ? "default" : "ghost"}>
          {busy ? "Starting…" : w === null ? "Start the pilot" : "Start a new pilot"}
        </Button>
        {running ? <span className="text-sm text-text-muted">Running. Starting a new pilot would reset the counts.</span> : null}
      </div>
      <div className="mt-2">
        <Problem>{problem}</Problem>
      </div>
    </Blueprint>
  );
}

function Report({ report }: { report: PilotReport }) {
  const a = report.alerts;
  const rated = a.false_rate !== null && a.false_rate !== undefined;
  const tiles: KpiTileProps[] = [
    {
      label: "Days used",
      value: `${report.days_used} of ${report.window.length_days}`,
      verdict: { tone: "unknown", text: "Days on which you opened the app." },
    },
    {
      label: "False alerts",
      value: rated ? formatPercent(a.false_rate, { decimals: 0 }) : "Not rated",
      verdict: {
        tone: !rated ? "unknown" : a.wrong_or_irrelevant === 0 ? "good" : "caution",
        text: rated ? `${a.wrong_or_irrelevant} of ${a.rated} rated alerts were wrong or irrelevant.` : `${a.sent} sent, none rated yet.`,
      },
    },
    {
      label: "Background jobs failed",
      value: `${report.jobs.failed} of ${report.jobs.runs}`,
      verdict: { tone: report.jobs.failed === 0 ? "good" : "bad", text: report.jobs.failed === 0 ? "Every run finished." : "See Activity for which ones." },
    },
    {
      label: "Portfolio flagged",
      value: formatPercent(report.affected_weight, { decimals: 0 }),
      verdict: { tone: report.top_issues.length === 0 ? "good" : "caution", text: "Share touched by at least one data issue today." },
    },
  ];

  const rows: StatusRow[] = [
    {
      id: "alerts",
      state: !rated ? "unknown" : a.wrong_or_irrelevant === 0 ? "pass" : "fail",
      lead: "Alerts",
      sentence: rated
        ? `${a.wrong_or_irrelevant} of ${a.rated} rated alerts were wrong or irrelevant (${formatPercent(a.false_rate, { decimals: 0 })}). ${a.sent} sent in all.`
        : `${a.sent} alert${a.sent === 1 ? "" : "s"} sent and none rated, so there is no false-alert rate. Rate an alert as wrong or irrelevant to count it.`,
    },
    {
      id: "jobs",
      state: report.jobs.failed === 0 ? "pass" : "fail",
      lead: "Background jobs",
      sentence: `${report.jobs.failed} of ${report.jobs.runs} runs failed during the pilot.`,
      href: "/activity#job-log",
      hrefLabel: "Open the job log",
    },
    ...report.providers.map(
      (p): StatusRow => ({
        id: `provider-${p.provider}`,
        state: p.failures === 0 ? "pass" : "fail",
        lead: titleCase(p.provider),
        sentence: `${p.failures} of ${p.attempts} requests failed.`,
      }),
    ),
    {
      id: "portfolio",
      state: report.top_issues.length === 0 ? "pass" : "fail",
      lead: "Portfolio data checks",
      sentence:
        report.top_issues.length === 0
          ? "No issues found by the checks that ran."
          : `${formatPercent(report.affected_weight, { decimals: 0 })} of the portfolio is touched by at least one data issue. Fix the largest first.`,
    },
  ];

  return (
    <Blueprint className="space-y-4 p-4">
      <PanelHeading level={2} title="How the pilot is going" />
      <KpiStrip label="Pilot at a glance" tiles={tiles} />
      <StatusList caption="Pilot measures" passWord="look healthy" rows={rows} />
      <details className="text-sm">
        <summary className="cursor-pointer font-semibold">Details: page use, corrections, issues and what is not measured</summary>
        <div className="mt-3 space-y-5">
          <div>
            <h3 className="text-sm font-semibold">Pages opened</h3>
            {report.use.length === 0 ? (
              <p className="mt-1 text-text-muted">No page has been opened since the pilot started.</p>
            ) : (
              <ResponsiveTable
                className="mt-1"
                rows={report.use}
                rowKey={(u) => u.path}
                caption="Pages opened during the pilot"
                columns={[
                  { key: "page", header: "Page", cell: (u) => u.path },
                  { key: "opens", header: "Opens", numeric: true, cell: (u) => u.opens },
                  { key: "days", header: "Days", numeric: true, cell: (u) => u.days },
                ]}
                compact={(u) => <CompactRow name={u.path} primary={`${u.opens} opens`} secondary={[`${u.days} days`]} />}
              />
            )}
          </div>
          <div>
            <h3 className="text-sm font-semibold">Corrections you made</h3>
            {report.corrections.length === 0 ? (
              <p className="mt-1 text-text-muted">None marked wrong.</p>
            ) : (
              <ul className="mt-1">
                {report.corrections.map((c) => (
                  <li key={c.target_kind}>
                    {c.count} marked wrong: {c.target_kind.replace("_", " ")}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {report.top_issues.length > 0 ? (
            <div>
              <h3 className="text-sm font-semibold">Largest data issues</h3>
              <ol className="mt-1 divide-y divide-border-soft border-y border-border-soft">
                {report.top_issues.map((i, n) => (
                  <li key={`${i.kind}-${i.subject}-${n}`} className="flex flex-wrap justify-between gap-x-3 py-1.5">
                    <span>
                      <span className="font-semibold">{i.subject}</span> <span className="text-text-muted">{ISSUE_LABEL[i.kind]}</span>
                    </span>
                    <span className="num">{i.weight === null || i.weight === undefined ? "No holding" : formatPercent(i.weight, { decimals: 1 })}</span>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
          <div>
            <h3 className="text-sm font-semibold">What this report cannot tell you</h3>
            <ul className="mt-1 list-disc space-y-1 pl-5 text-text-muted">
              {report.not_measured.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </div>
        </div>
      </details>
    </Blueprint>
  );
}
