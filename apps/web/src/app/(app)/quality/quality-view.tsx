"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { ISSUE_LABEL, SNOOZE_CHOICES, snoozeUntil, weightBar, type Cockpit, type QualityIssue } from "@/lib/journal/journal";

import { InsightCoveragePanel } from "./insight-coverage-panel";

const fetchCockpit = () => getBrowserApiClient().GET("/quality/cockpit");

export function QualityView() {
  const { state, reload } = useApiResource(fetchCockpit);
  return (
    <ResourceGate state={state} label="the data-quality checks">
      {(cockpit) => <Body cockpit={cockpit} onChanged={reload} />}
    </ResourceGate>
  );
}

/** Set one issue aside until a date, or bring it back. The check keeps running either way. */
function SnoozeControl({ issue, onChanged }: { issue: QualityIssue; onChanged: () => void }) {
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  async function send(action: "snooze" | "clear") {
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/quality/snooze", {
      body: { kind: issue.kind, subject: issue.subject, action, until: action === "snooze" ? snoozeUntil(days) : null },
    });
    setBusy(false);
    if (!res.response.ok) return setProblem("That could not be saved. Try again.");
    onChanged();
  }
  return (
    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
      <label className="flex items-center gap-1">
        <span className="text-text-muted">Set aside for</span>
        <select className="h-7 border border-border bg-surface px-1" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {SNOOZE_CHOICES.map((c) => (
            <option key={c.days} value={c.days}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <button type="button" disabled={busy} onClick={() => void send("snooze")} className="h-7 border border-border px-2 hover:bg-surface-muted disabled:opacity-60">
        Set aside
      </button>
      {problem ? (
        <span className="text-loss" role="alert">
          {problem}
        </span>
      ) : null}
    </div>
  );
}

function BringBack({ issue, onChanged }: { issue: QualityIssue; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      className="h-7 border border-border px-2 text-xs hover:bg-surface-muted disabled:opacity-60"
      onClick={async () => {
        setBusy(true);
        await getBrowserApiClient().POST("/quality/snooze", { body: { kind: issue.kind, subject: issue.subject, action: "clear", until: null } });
        setBusy(false);
        onChanged();
      }}
    >
      Bring back now
    </button>
  );
}

function Body({ cockpit, onChanged }: { cockpit: Cockpit; onChanged: () => void }) {
  const max = Math.max(0, ...cockpit.issues.map((i) => i.weight ?? 0));
  const portfolio = cockpit.issues.filter((i) => i.weight !== null && i.weight !== undefined);
  const other = cockpit.issues.filter((i) => i.weight === null || i.weight === undefined);
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="p-4">
        <PanelHeading
          level={2}
          kicker="Summary"
          title={cockpit.issues.length === 0 ? "No problems found by the checks that ran" : `${cockpit.issues.length} issue${cockpit.issues.length === 1 ? "" : "s"} to look at`}
        />
        <p className="mt-2 max-w-prose text-sm">
          {cockpit.issues.length === 0
            ? "Every check below ran and found nothing. The checks listed as not covered did not run."
            : `Together they touch ${formatPercent(cockpit.affected_weight, { decimals: 0 })} of the portfolio, counting each holding once.`}
        </p>
      </Blueprint>

      {portfolio.length > 0 ? (
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Fix first" title="Issues on holdings, by share of the portfolio" />
          <ol className="mt-3 divide-y divide-border-soft border-y border-border-soft">
            {portfolio.map((issue, i) => (
              <li key={`${issue.kind}-${issue.subject}-${i}`} className="grid gap-x-4 gap-y-1 py-2.5 sm:grid-cols-[9rem_1fr_6rem]">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold">{issue.subject}</div>
                  <div className="text-xs text-text-muted">{ISSUE_LABEL[issue.kind]}</div>
                </div>
                <div className="min-w-0 text-sm">
                  <p>{issue.detail}</p>
                  <p className="text-xs text-text-muted">{issue.fix}</p>
                  <SnoozeControl issue={issue} onChanged={onChanged} />
                </div>
                <div className="flex items-center gap-2 sm:flex-col sm:items-end sm:gap-1">
                  <span className="num text-sm font-semibold">{formatPercent(issue.weight ?? 0, { decimals: 1 })}</span>
                  <span className="block h-1.5 w-full max-w-24 bg-border-soft" aria-hidden="true">
                    <span className="block h-full bg-accent" style={{ width: `${weightBar(issue.weight, max)}%` }} />
                  </span>
                </div>
              </li>
            ))}
          </ol>
        </Blueprint>
      ) : null}

      {other.length > 0 ? (
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Not tied to a holding" title="Sources and providers" />
          <ul className="mt-3 divide-y divide-border-soft border-y border-border-soft">
            {other.map((issue, i) => (
              <li key={`${issue.kind}-${issue.subject}-${i}`} className="py-2.5 text-sm">
                <span className="font-semibold">{issue.subject}</span> <span className="text-text-muted">· {ISSUE_LABEL[issue.kind]}</span>
                <p>{issue.detail}</p>
                <p className="text-xs text-text-muted">{issue.fix}</p>
                <SnoozeControl issue={issue} onChanged={onChanged} />
              </li>
            ))}
          </ul>
        </Blueprint>
      ) : null}

      {(cockpit.snoozed ?? []).length > 0 ? (
        <Blueprint className="border-dashed p-4">
          <PanelHeading level={3} kicker="Set aside" title="Hidden until the date shown" />
          <ul className="mt-3 divide-y divide-border-soft border-y border-border-soft text-sm">
            {(cockpit.snoozed ?? []).map((s) => (
              <li key={`${s.issue.kind}-${s.issue.subject}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <span className="font-semibold">{s.issue.subject}</span> <span className="text-text-muted">{ISSUE_LABEL[s.issue.kind]}: {s.issue.detail}</span>
                  <span className="block text-xs text-text-muted">Comes back on {formatDate(s.until)}</span>
                </span>
                <BringBack issue={s.issue} onChanged={onChanged} />
              </li>
            ))}
          </ul>
        </Blueprint>
      ) : null}

      <div className="grid gap-5 lg:grid-cols-2">
        <Blueprint className="p-4">
          <PanelHeading level={3} kicker="Checks that ran" title="And what each one covers" />
          <table className="mt-3 w-full text-sm">
            <caption className="sr-only">Data checks that ran, what each covers and how many issues it found</caption>
            <thead>
              <tr className="text-left text-xs text-text-muted">
                <th scope="col" className="py-1 font-medium">
                  Check
                </th>
                <th scope="col" className="py-1 text-right font-medium">
                  Found
                </th>
              </tr>
            </thead>
            <tbody>
              {cockpit.checks.map((c) => (
                <tr key={c.kind} className="border-t border-border-soft align-top">
                  <th scope="row" className="py-1.5 text-left font-normal">
                    {c.title}
                    <div className="text-xs text-text-muted">Looked at: {c.denominator}</div>
                  </th>
                  <td className="num py-1.5 text-right">{c.issues}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Blueprint>
        <Blueprint className="border-dashed p-4">
          <PanelHeading level={3} kicker="Not covered yet" title="Checks that cannot run" />
          <ul className="mt-3 space-y-2 text-sm">
            {cockpit.not_covered.map((n) => (
              <li key={n.title}>
                <span className="font-semibold">{n.title}</span>
                <p className="text-text-muted">{n.reason}</p>
              </li>
            ))}
          </ul>
        </Blueprint>
      </div>

      <InsightCoveragePanel />
    </div>
  );
}
