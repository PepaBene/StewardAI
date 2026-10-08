"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Problem, problemText } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { ISSUE_LABEL } from "@/lib/journal/journal";
import { pilotDayLabel, type PilotReport, type PilotState } from "@/lib/release/release";

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
    if (!res.response.ok) return setProblem(problemText(res.error, "The pilot could not be started."));
    onStarted();
  }

  return (
    <Blueprint className="p-4">
      <PanelHeading
        level={2}
        kicker="30-day pilot"
        title={w === null ? "No pilot has started" : pilotDayLabel(w.day, w.length_days, w.over)}
      />
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
  return (
    <>
      <div className="grid gap-5 lg:grid-cols-2">
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Use" title={`${report.days_used} of ${report.window.length_days} days used`} />
          {report.use.length === 0 ? (
            <p className="mt-2 text-sm text-text-muted">No page has been opened since the pilot started.</p>
          ) : (
            <table className="mt-3 w-full text-sm">
              <caption className="sr-only">Pages opened during the pilot</caption>
              <thead>
                <tr className="text-left text-xs text-text-muted">
                  <th scope="col" className="py-1 font-medium">
                    Page
                  </th>
                  <th scope="col" className="py-1 text-right font-medium">
                    Opens
                  </th>
                  <th scope="col" className="py-1 text-right font-medium">
                    Days
                  </th>
                </tr>
              </thead>
              <tbody>
                {report.use.map((u) => (
                  <tr key={u.path} className="border-t border-border-soft">
                    <th scope="row" className="py-1.5 text-left font-normal">
                      {u.path}
                    </th>
                    <td className="num py-1.5 text-right">{u.opens}</td>
                    <td className="num py-1.5 text-right">{u.days}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Blueprint>

        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Alerts" title="False alerts" />
          <p className="mt-2 text-sm">
            {a.false_rate === null || a.false_rate === undefined
              ? `${a.sent} alert${a.sent === 1 ? "" : "s"} sent and none rated, so there is no false-alert rate. Rate an alert as wrong or irrelevant to count it.`
              : `${a.wrong_or_irrelevant} of ${a.rated} rated alerts were wrong or irrelevant (${formatPercent(a.false_rate, { decimals: 0 })}). ${a.sent} sent in all.`}
          </p>
          <h4 className="mt-4 text-sm font-semibold">Corrections you made</h4>
          {report.corrections.length === 0 ? (
            <p className="text-sm text-text-muted">None marked wrong.</p>
          ) : (
            <ul className="mt-1 text-sm">
              {report.corrections.map((c) => (
                <li key={c.target_kind}>
                  {c.count} × {c.target_kind.replace("_", " ")}
                </li>
              ))}
            </ul>
          )}
        </Blueprint>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Sources" title="Failures by provider" />
          <p className="mt-2 text-sm">
            {report.jobs.failed} of {report.jobs.runs} background jobs failed.
          </p>
          {report.providers.length === 0 ? (
            <p className="mt-1 text-sm text-text-muted">No provider requests were recorded.</p>
          ) : (
            <table className="mt-2 w-full text-sm">
              <caption className="sr-only">Provider requests and failures during the pilot</caption>
              <thead>
                <tr className="text-left text-xs text-text-muted">
                  <th scope="col" className="py-1 font-medium">
                    Provider
                  </th>
                  <th scope="col" className="py-1 text-right font-medium">
                    Failed
                  </th>
                  <th scope="col" className="py-1 text-right font-medium">
                    Of
                  </th>
                </tr>
              </thead>
              <tbody>
                {report.providers.map((p) => (
                  <tr key={p.provider} className="border-t border-border-soft">
                    <th scope="row" className="py-1.5 text-left font-normal">
                      {p.provider}
                    </th>
                    <td className="num py-1.5 text-right">{p.failures}</td>
                    <td className="num py-1.5 text-right">{p.attempts}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Blueprint>

        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Data today" title="Share of the portfolio flagged" />
          <p className="mt-2 text-sm">
            {formatPercent(report.affected_weight, { decimals: 0 })} of the portfolio is touched by at least one data issue today. Fix the largest first:
          </p>
          {report.top_issues.length === 0 ? (
            <p className="mt-1 text-sm text-text-muted">No issues found by the checks that ran.</p>
          ) : (
            <ol className="mt-2 divide-y divide-border-soft border-y border-border-soft text-sm">
              {report.top_issues.map((i, n) => (
                <li key={`${i.kind}-${i.subject}-${n}`} className="flex flex-wrap justify-between gap-x-3 py-1.5">
                  <span>
                    <span className="font-semibold">{i.subject}</span> <span className="text-text-muted">{ISSUE_LABEL[i.kind]}</span>
                  </span>
                  <span className="num">{i.weight === null || i.weight === undefined ? "no holding" : formatPercent(i.weight, { decimals: 1 })}</span>
                </li>
              ))}
            </ol>
          )}
        </Blueprint>
      </div>

      <Blueprint className="border-dashed p-4">
        <PanelHeading level={3} kicker="Not measured" title="What this report cannot tell you" />
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-text-muted">
          {report.not_measured.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </Blueprint>
    </>
  );
}
