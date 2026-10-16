"use client";

import Link from "next/link";
import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { EntityName } from "@/components/entity-name";
import { CheckStateIcon } from "@/components/insight/check-list";
import { KpiStrip } from "@/components/insight/kpi-tile";
import { StatusList, type StatusRow } from "@/components/oversight/status-list";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { ISSUE_LABEL, SNOOZE_CHOICES, snoozeUntil, type Cockpit, type QualityIssue } from "@/lib/journal/journal";
import { sentenceCase, subjectWords } from "@/lib/oversight/plain";

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

/** Set one issue aside until a date. The check keeps running either way. */
function SnoozeControl({ issue, onChanged }: { issue: QualityIssue; onChanged: () => void }) {
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  async function send() {
    setBusy(true);
    setProblem(null);
    const res = await getBrowserApiClient().POST("/quality/snooze", {
      body: { kind: issue.kind, subject: issue.subject, action: "snooze", until: snoozeUntil(days) },
    });
    setBusy(false);
    if (!res.response.ok) return setProblem("That could not be saved. Try again.");
    onChanged();
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <label className="flex items-center gap-1">
        <span className="sr-only">Set aside for</span>
        <select
          className="h-7 rounded-control border border-border bg-surface px-1"
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          aria-label={`Set aside ${issue.subject} for`}
        >
          {SNOOZE_CHOICES.map((c) => (
            <option key={c.days} value={c.days}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void send()}>
        Set aside
      </Button>
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
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        await getBrowserApiClient().POST("/quality/snooze", { body: { kind: issue.kind, subject: issue.subject, action: "clear", until: null } });
        setBusy(false);
        onChanged();
      }}
    >
      Bring back now
    </Button>
  );
}

/** An instrument id ("GOOGL_US_EQ") shows as its company name; any other subject as words. */
function Subject({ subject }: { subject: string }) {
  if (/_EQ$/.test(subject)) return <EntityName id={subject} chip={false} />;
  return <>{subjectWords(subject)}</>;
}

/**
 * One kind of issue as one block: what it is and how much of the portfolio it touches, said once,
 * with the fix, then every affected holding by name with its share and its own "Set aside".
 */
function IssueGroup({ kind, list, onChanged }: { kind: QualityIssue["kind"]; list: QualityIssue[]; onChanged: () => void }) {
  const weight = list.reduce((s, i) => s + (i.weight ?? 0), 0);
  const sameDetail = list.every((i) => i.detail === list[0].detail);
  const sameFix = list.every((i) => i.fix === list[0].fix);
  const many = list.length > 1;
  return (
    <div className="rounded-card bg-surface-muted px-4 py-3">
      <div className="flex gap-2.5">
        <CheckStateIcon state="fail" className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">
            {ISSUE_LABEL[kind]}
            <span className="font-normal text-text-muted">
              {many ? `, ${list.length} ${list.length === 1 ? "item" : "items"}` : ""}
              {weight > 0 ? `, ${formatPercent(weight, { decimals: 1 })} of the portfolio` : ""}
            </span>
          </h3>
          {many && sameDetail ? <p className="text-sm">{sentenceCase(list[0].detail)}.</p> : null}
          {sameFix ? <p className="text-sm text-text-muted">{list[0].fix}</p> : null}
        </div>
      </div>
      <details open={!many || undefined} className="mt-2">
        {many ? <summary className="cursor-pointer text-sm font-medium text-accent-ink">Show the {list.length} items</summary> : null}
        <ul className="mt-1 divide-y divide-border-soft">
          {list.map((issue, i) => (
            <li key={`${issue.subject}-${i}`} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2 text-sm">
              <span className="min-w-0">
                <span className="font-medium">
                  <Subject subject={issue.subject} />
                </span>
                {issue.weight !== null && issue.weight !== undefined ? (
                  <span className="num text-text-muted">, {formatPercent(issue.weight, { decimals: 1 })} of the portfolio</span>
                ) : null}
                {!(many && sameDetail) ? <span className="block text-text-muted">{sentenceCase(issue.detail)}.</span> : null}
                {!sameFix ? <span className="block text-xs text-text-muted">{issue.fix}</span> : null}
              </span>
              <SnoozeControl issue={issue} onChanged={onChanged} />
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function Body({ cockpit, onChanged }: { cockpit: Cockpit; onChanged: () => void }) {
  const issues = cockpit.issues;
  const snoozed = cockpit.snoozed ?? [];
  const kinds = [...new Set(issues.map((i) => i.kind))];
  const checkRows: StatusRow[] = [
    ...cockpit.checks.map(
      (c): StatusRow => ({
        id: `check-${c.kind}`,
        state: c.issues > 0 ? "fail" : "pass",
        lead: c.title,
        sentence: c.issues > 0 ? `Found ${c.issues} to look at. Looked at: ${c.denominator}.` : `Found nothing. Looked at: ${c.denominator}.`,
      }),
    ),
    ...cockpit.not_covered.map(
      (n): StatusRow => ({ id: `nc-${n.title}`, state: "unknown", lead: n.title, sentence: n.reason }),
    ),
  ];
  return (
    <div className="flex flex-col gap-5">
      <KpiStrip
        label="Data quality at a glance"
        tiles={[
          {
            label: "Portfolio affected",
            value: formatPercent(cockpit.affected_weight, { decimals: 0 }),
            verdict: {
              tone: cockpit.affected_weight > 0.25 ? "bad" : cockpit.affected_weight > 0 ? "caution" : "good",
              text: issues.length === 0 ? "No holding is affected." : "Share of the portfolio touched by an issue, counting each holding once.",
            },
          },
          {
            label: "Open issues",
            value: issues.length,
            verdict: { tone: issues.length === 0 ? "good" : "caution", text: issues.length === 0 ? "Every check that ran found nothing." : "Fix the top of the list first." },
          },
          {
            label: "Set aside",
            value: snoozed.length,
            verdict: { tone: "neutral", text: snoozed.length === 0 ? "Nothing hidden." : "Hidden until the date shown below." },
          },
          {
            label: "Checks that cannot run",
            value: cockpit.not_covered.length,
            verdict: {
              tone: cockpit.not_covered.length === 0 ? "good" : "unknown",
              text: cockpit.not_covered.length === 0 ? "Every check can run." : "Listed with the reason, so a gap is never read as clean.",
            },
          },
        ]}
      />

      <section aria-labelledby="issues-heading" className="blueprint space-y-3 px-[22px] py-5">
        <div>
          <h2 id="issues-heading" className="panel-title">
            {issues.length === 0 ? "No problems found by the checks that ran" : `${kinds.length} ${kinds.length === 1 ? "kind" : "kinds"} of issue to look at`}
          </h2>
          <p className="text-sm text-text-muted">
            {issues.length} {issues.length === 1 ? "item" : "items"} in all, the kind touching most of the portfolio first. Set an item aside and the check keeps running.
          </p>
        </div>
        {kinds
          .map((kind) => ({ kind, list: issues.filter((i) => i.kind === kind) }))
          .sort((a, b) => b.list.reduce((s, i) => s + (i.weight ?? 0), 0) - a.list.reduce((s, i) => s + (i.weight ?? 0), 0))
          .map(({ kind, list }) => (
            <IssueGroup key={kind} kind={kind} list={list} onChanged={onChanged} />
          ))}
      </section>

      <div className="grid items-start gap-5 lg:grid-cols-2">
        <section aria-labelledby="checks-heading" className="blueprint space-y-2 px-[22px] py-5">
          <h2 id="checks-heading" className="panel-title">
            What each check covers
          </h2>
          <StatusList caption="Data checks and what each covers" passWord="found nothing" rows={checkRows} />
        </section>
        <section aria-labelledby="more-heading" className="blueprint space-y-2 px-[22px] py-5">
          <h2 id="more-heading" className="panel-title">
            Other coverage views
          </h2>
          <ul className="space-y-1 text-sm">
            <li>
              <Link href="/disclosures/coverage" className="text-accent-ink underline underline-offset-2">
                Filings coverage
              </Link>{" "}
              <span className="text-text-muted">which holdings have filings on record.</span>
            </li>
            <li>
              <Link href="/activity" className="text-accent-ink underline underline-offset-2">
                Activity
              </Link>{" "}
              <span className="text-text-muted">price and earnings coverage per holding, and job runs.</span>
            </li>
            <li>
              <Link href="/status" className="text-accent-ink underline underline-offset-2">
                Status
              </Link>{" "}
              <span className="text-text-muted">reference data gaps and API health.</span>
            </li>
          </ul>
        </section>
      </div>

      {snoozed.length > 0 ? (
        <details className="blueprint p-5">
          <summary className="cursor-pointer text-lg font-semibold">Set aside ({snoozed.length})</summary>
          <ul className="mt-3 divide-y divide-border-soft text-sm">
            {snoozed.map((s) => (
              <li key={`${s.issue.kind}-${s.issue.subject}`} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <span className="font-semibold">
                    <Subject subject={s.issue.subject} />
                  </span>{" "}
                  <span className="text-text-muted">
                    {ISSUE_LABEL[s.issue.kind]}: {s.issue.detail}
                  </span>
                  <span className="block text-xs text-text-muted">Comes back on {formatDate(s.until)}</span>
                </span>
                <BringBack issue={s.issue} onChanged={onChanged} />
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <InsightCoveragePanel />
    </div>
  );
}
