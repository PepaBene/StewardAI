"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Field, Problem, problemText, SELECT, TEXTAREA } from "@/components/risk/common";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  buildAudit,
  EMPTY_AUDIT,
  FEATURE_CHOICES,
  KIND_LABEL,
  KIND_ONE,
  progressLabel,
  progressShare,
  RESULT_LABEL,
  type AuditForm,
  type AuditOverview,
  type AuditRow,
  type SubjectKind,
} from "@/lib/release/release";
import { cn } from "@/lib/utils";

const fetchAudit = () => getBrowserApiClient().GET("/release/audit", { params: { query: {} } });

export function AuditView() {
  const { state, reload } = useApiResource(fetchAudit);
  const [form, setForm] = useState<AuditForm>(EMPTY_AUDIT);
  return (
    <div className="flex flex-col gap-5">
      <ResourceGate state={state} label="the source audit">
        {(overview) => <Progress overview={overview} onRecheck={(row) => setForm(recheck(row))} />}
      </ResourceGate>
      <AuditFormPanel form={form} setForm={setForm} onSaved={reload} />
      <ResourceGate state={state} label="the audit history">
        {(overview) => <History rows={overview.rows} onRecheck={(row) => setForm(recheck(row))} />}
      </ResourceGate>
    </div>
  );
}

/** A re-check starts from the same fact; the new row replaces the old result without erasing it. */
function recheck(row: AuditRow): AuditForm {
  return {
    ...EMPTY_AUDIT,
    subject_kind: row.subject_kind,
    subject: row.subject,
    region: row.region ?? "",
    feature: row.feature,
    field: row.field,
    displayed: row.displayed,
  };
}

function Progress({ overview, onRecheck }: { overview: AuditOverview; onRecheck: (row: AuditRow) => void }) {
  const { summary } = overview;
  return (
    <Blueprint className="p-4">
      <PanelHeading
        level={2}
        kicker="Sample so far"
        title={summary.complete ? "The sample is complete and clean" : "The sample is not complete yet"}
      />
      <p className="mt-2 max-w-prose text-sm">
        Compare what the app shows with the filing, issuer report or official statistic it cites. Only subjects whose every checked fact matches count toward the target. {summary.facts_checked} fact
        {summary.facts_checked === 1 ? "" : "s"} checked: {summary.facts_matching} match, {summary.facts_mismatched} differ, {summary.facts_not_checkable} could not be checked.
      </p>
      <table className="mt-3 w-full text-sm">
        <caption className="sr-only">Audit progress against the planned sample</caption>
        <thead>
          <tr className="text-left text-xs text-text-muted">
            <th scope="col" className="py-1 font-medium">
              Subjects
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              Clean of target
            </th>
            <th scope="col" className="py-1 pl-4 font-medium">
              <span className="sr-only">Progress</span>
            </th>
            <th scope="col" className="py-1 text-right font-medium">
              Differ
            </th>
          </tr>
        </thead>
        <tbody>
          {summary.targets.map((p) => (
            <tr key={p.subject_kind} className="border-t border-border-soft">
              <th scope="row" className="py-1.5 text-left font-normal">
                {KIND_LABEL[p.subject_kind as SubjectKind]}
              </th>
              <td className="num py-1.5 text-right">{progressLabel(p)}</td>
              <td className="w-1/4 py-1.5 pl-4" aria-hidden="true">
                <span className="block h-1.5 w-full bg-border-soft">
                  <span className="block h-full bg-accent" style={{ width: `${progressShare(p) * 100}%` }} />
                </span>
              </td>
              <td className={cn("num py-1.5 text-right", p.with_mismatch > 0 && "font-semibold")}>{p.with_mismatch}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-sm">
        Regions among the securities: {summary.regions.length > 0 ? summary.regions.join(", ") : "none yet"}. At least {summary.regions_needed} are needed
        {summary.regions.length >= summary.regions_needed ? " (met)." : `; ${summary.regions_needed - summary.regions.length} more.`}
      </p>
      {summary.open_mismatches.length > 0 ? (
        <div className="mt-3 border-l-2 border-warning-signal bg-warning-signal/5 px-3 py-2 text-sm">
          <p className="font-semibold">
            {summary.open_mismatches.length} fact{summary.open_mismatches.length === 1 ? "" : "s"} differ from the source and block their feature
          </p>
          <ul className="mt-1 space-y-1">
            {summary.open_mismatches.map((m) => (
              <li key={m.id} className="flex flex-wrap items-baseline gap-x-2">
                <span>
                  {m.subject}: {m.field} (screen {m.displayed}, source {m.source_value})
                </span>
                <button type="button" onClick={() => onRecheck(m)} className="text-xs underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus">
                  Re-check after the fix
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Blueprint>
  );
}

function AuditFormPanel({ form, setForm, onSaved }: { form: AuditForm; setForm: (f: AuditForm) => void; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const set = <K extends keyof AuditForm>(key: K, value: AuditForm[K]) => setForm({ ...form, [key]: value });
  const checkable = form.result !== "not_checkable";

  async function save() {
    const built = buildAudit(form);
    if (!built.ok) return setProblem(built.error);
    setBusy(true);
    setProblem(null);
    setDone(false);
    const res = await getBrowserApiClient().POST("/release/audit", { body: built.body });
    setBusy(false);
    if (!res.response.ok) return setProblem(problemText(res.error, "The check could not be saved."));
    setForm({ ...EMPTY_AUDIT, subject_kind: form.subject_kind, feature: form.feature });
    setDone(true);
    onSaved();
  }

  return (
    <Blueprint className="p-4">
      <PanelHeading level={2} kicker="New comparison" title="Record one fact checked against its source" />
      <p className="mt-1 max-w-prose text-sm text-text-muted">
        A saved check is never edited. If you were wrong, or the app was fixed, record the same fact again; the newest result stands and the old one stays in the history.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-4">
        <Field label="What you checked">
          <select className={SELECT} value={form.subject_kind} onChange={(e) => set("subject_kind", e.target.value as SubjectKind)}>
            {(Object.keys(KIND_ONE) as SubjectKind[]).map((k) => (
              <option key={k} value={k}>
                {KIND_ONE[k]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Name" hint="Ticker, ISIN or window">
          <Input value={form.subject} onChange={(e) => set("subject", e.target.value)} maxLength={80} />
        </Field>
        <Field label="Region" hint={form.subject_kind === "security" ? "Required for securities" : "Optional"}>
          <Input value={form.region} onChange={(e) => set("region", e.target.value)} maxLength={40} />
        </Field>
        <Field label="Feature it belongs to">
          <select className={SELECT} value={form.feature} onChange={(e) => set("feature", e.target.value)}>
            {FEATURE_CHOICES.map((f) => (
              <option key={f.id} value={f.id}>
                {f.label}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Field label="Which fact" hint="For example revenue 2025, ongoing charge, split ratio">
          <Input value={form.field} onChange={(e) => set("field", e.target.value)} maxLength={120} />
        </Field>
        <Field label="What the screen shows">
          <Input value={form.displayed} onChange={(e) => set("displayed", e.target.value)} maxLength={400} />
        </Field>
        <Field label="Result">
          <select className={SELECT} value={form.result} onChange={(e) => set("result", e.target.value as AuditForm["result"])}>
            {(Object.keys(RESULT_LABEL) as AuditForm["result"][]).map((r) => (
              <option key={r} value={r}>
                {RESULT_LABEL[r]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Value in the source" hint={checkable ? "Required" : "Not needed"}>
          <Input value={form.source_value} disabled={!checkable} onChange={(e) => set("source_value", e.target.value)} maxLength={400} />
        </Field>
        <Field label="Link to the source" hint={checkable ? "Filing, issuer report or official statistic" : "Not needed"} className="sm:col-span-2">
          <Input type="url" value={form.source_url} disabled={!checkable} onChange={(e) => set("source_url", e.target.value)} maxLength={1000} />
        </Field>
        <Field label="Note" hint="Why it differs, or why it could not be checked" className="sm:col-span-2">
          <textarea className={TEXTAREA} value={form.note} onChange={(e) => set("note", e.target.value)} maxLength={1000} />
        </Field>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button type="button" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save check"}
        </Button>
        {done ? <span role="status" className="text-sm text-text-muted">Check saved.</span> : null}
      </div>
      <div className="mt-2">
        <Problem>{problem}</Problem>
      </div>
    </Blueprint>
  );
}

function History({ rows, onRecheck }: { rows: AuditRow[]; onRecheck: (row: AuditRow) => void }) {
  if (rows.length === 0) {
    return (
      <Blueprint className="border-dashed p-4">
        <PanelHeading level={3} kicker="History" title="Nothing compared yet" />
        <p className="mt-2 max-w-prose text-sm text-text-muted">
          Pick a security the app shows, open the filing it cites, and record the first fact above. Aim for twenty securities in at least three regions, five funds, three corporate-action histories and two cash-flow windows.
        </p>
      </Blueprint>
    );
  }
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} kicker="History" title="Every comparison, newest first" />
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[40rem] text-sm">
          <caption className="sr-only">Recorded source comparisons</caption>
          <thead>
            <tr className="text-left text-xs text-text-muted">
              <th scope="col" className="py-1 font-medium">
                Subject and fact
              </th>
              <th scope="col" className="py-1 font-medium">
                Screen
              </th>
              <th scope="col" className="py-1 font-medium">
                Source
              </th>
              <th scope="col" className="py-1 font-medium">
                Result
              </th>
              <th scope="col" className="py-1 text-right font-medium">
                Date
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-border-soft align-top">
                <th scope="row" className="py-1.5 pr-3 text-left font-normal">
                  <span className="font-semibold">{r.subject}</span> <span className="text-text-muted">({KIND_ONE[r.subject_kind]})</span>
                  <div className="text-xs text-text-muted">{r.field}</div>
                </th>
                <td className="py-1.5 pr-3">{r.displayed}</td>
                <td className="py-1.5 pr-3">
                  {r.source_value ?? "—"}
                  {r.source_url ? (
                    <a href={r.source_url} target="_blank" rel="noreferrer noopener" className="ml-1 text-xs underline underline-offset-2">
                      open source
                    </a>
                  ) : null}
                </td>
                <td className="py-1.5 pr-3">
                  {RESULT_LABEL[r.result]}
                  {r.note ? <div className="text-xs text-text-muted">{r.note}</div> : null}
                  <button type="button" onClick={() => onRecheck(r)} className="text-xs underline underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus">
                    Check again
                  </button>
                </td>
                <td className="num py-1.5 text-right whitespace-nowrap">{formatDate(r.audited_on)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Blueprint>
  );
}
