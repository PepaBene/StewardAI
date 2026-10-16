"use client";

import { useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { Field, Problem, problemText, SELECT, TEXTAREA } from "@/components/risk/common";
import { KpiStrip, type KpiTileProps } from "@/components/insight/kpi-tile";
import { StatusList, type StatusRow } from "@/components/oversight/status-list";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { CompactRow } from "@/components/ui/compact-row";
import { ResponsiveTable } from "@/components/ui/responsive-table";
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

const fetchAudit = () => getBrowserApiClient().GET("/release/audit", { params: { query: {} } });

export function AuditView() {
  const { state, reload } = useApiResource(fetchAudit);
  const [form, setForm] = useState<AuditForm>(EMPTY_AUDIT);
  return (
    <div className="flex flex-col gap-5">
      <ResourceGate state={state} label="the source audit">
        {(overview) => <Progress overview={overview} onRecheck={(row) => setForm(recheck(row))} />}
      </ResourceGate>
      <details className="group rounded-card bg-surface-muted px-5 py-3">
        <summary className="cursor-pointer text-[15px] font-semibold">Details: record a check and see every comparison</summary>
        <div className="mt-3 flex flex-col gap-5">
          <AuditFormPanel form={form} setForm={setForm} onSaved={reload} />
          <ResourceGate state={state} label="the audit history">
            {(overview) => <History rows={overview.rows} onRecheck={(row) => setForm(recheck(row))} />}
          </ResourceGate>
        </div>
      </details>
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
  const regionsMet = summary.regions.length >= summary.regions_needed;
  const cleanTotal = summary.targets.reduce((n, p) => n + p.clean, 0);
  const targetTotal = summary.targets.reduce((n, p) => n + p.target, 0);
  const tiles: KpiTileProps[] = [
    {
      label: "Subjects clean",
      value: `${cleanTotal} of ${targetTotal}`,
      verdict: { tone: summary.complete ? "good" : "caution", text: summary.complete ? "Every target is met." : "Keep comparing until each target is met." },
    },
    {
      label: "Facts matching",
      value: `${summary.facts_matching} of ${summary.facts_checked}`,
      verdict: { tone: "unknown", text: `${summary.facts_not_checkable} could not be checked.` },
    },
    {
      label: "Facts that differ",
      value: summary.facts_mismatched,
      verdict: { tone: summary.open_mismatches.length > 0 ? "bad" : "good", text: summary.open_mismatches.length > 0 ? "They block their feature until fixed." : "Nothing blocks a feature." },
    },
    {
      label: "Regions covered",
      value: `${summary.regions.length} of ${summary.regions_needed}`,
      verdict: { tone: regionsMet ? "good" : "caution", text: summary.regions.length > 0 ? summary.regions.join(", ") : "None yet." },
    },
  ];
  const rows: StatusRow[] = [
    ...summary.targets.map(
      (p): StatusRow => ({
        id: `target-${p.subject_kind}`,
        state: p.with_mismatch > 0 || p.clean < p.target ? "fail" : "pass",
        lead: KIND_LABEL[p.subject_kind as SubjectKind],
        sentence:
          p.with_mismatch > 0
            ? `${progressLabel(p)} clean; ${p.with_mismatch} differ from the source.`
            : p.clean >= p.target
              ? `Target met: ${progressLabel(p)} clean.`
              : `${progressLabel(p)} clean; ${p.target - p.clean} more to compare.`,
        details: <p>{Math.round(progressShare(p) * 100)}% of the target.</p>,
      }),
    ),
    {
      id: "regions",
      state: regionsMet ? "pass" : "fail",
      lead: "Regions among the securities",
      sentence: regionsMet ? `Met: ${summary.regions.length} of at least ${summary.regions_needed}.` : `${summary.regions_needed - summary.regions.length} more region${summary.regions_needed - summary.regions.length === 1 ? "" : "s"} needed.`,
    },
    ...summary.open_mismatches.map(
      (m): StatusRow => ({
        id: `mismatch-${m.id}`,
        state: "fail",
        lead: `${m.subject}: ${m.field}`,
        sentence: `Differs from the source (screen ${m.displayed}, source ${m.source_value ?? "not given"}) and blocks its feature.`,
        action: (
          <Button type="button" size="sm" variant="outline" onClick={() => onRecheck(m)}>
            Re-check after the fix
          </Button>
        ),
      }),
    ),
  ];
  return (
    <Blueprint className="space-y-4 p-4">
      <PanelHeading level={2} title={summary.complete ? "The sample is complete and clean" : "The sample is not complete yet"} />
      <p className="max-w-prose text-sm text-text-muted">
        Compare what the app shows with the filing, issuer report or official statistic it cites. Only subjects whose every checked fact matches count toward the target.
      </p>
      <KpiStrip label="Source audit at a glance" tiles={tiles} />
      <StatusList caption="Audit targets" passWord="targets met" rows={rows} />
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
      <PanelHeading level={2} title="Record one fact checked against its source" />
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
        <PanelHeading level={3} title="Nothing compared yet" />
        <p className="mt-2 max-w-prose text-sm text-text-muted">
          Pick a security the app shows, open the filing it cites, and record the first fact above. Aim for twenty securities in at least three regions, five funds, three corporate-action histories and two cash-flow windows.
        </p>
      </Blueprint>
    );
  }
  return (
    <Blueprint className="p-4">
      <PanelHeading level={3} title="Every comparison, newest first" />
      <ResponsiveTable
        className="mt-3"
        rows={rows}
        rowKey={(r) => String(r.id)}
        caption="Recorded source comparisons"
        columns={[
          {
            key: "subject",
            header: "Subject and fact",
            cell: (r) => (
              <>
                <span className="font-semibold">{r.subject}</span> <span className="text-text-muted">({KIND_ONE[r.subject_kind]})</span>
                <div className="text-xs text-text-muted">{r.field}</div>
              </>
            ),
          },
          { key: "screen", header: "Screen", cell: (r) => r.displayed },
          {
            key: "source",
            header: "Source",
            cell: (r) => (
              <>
                {r.source_value ?? "None given"}
                {r.source_url ? (
                  <a href={r.source_url} target="_blank" rel="noreferrer noopener" className="ml-1 text-xs underline underline-offset-2">
                    Open source
                  </a>
                ) : null}
              </>
            ),
          },
          {
            key: "result",
            header: "Result",
            cell: (r) => (
              <>
                {RESULT_LABEL[r.result]}
                {r.note ? <div className="text-xs text-text-muted">{r.note}</div> : null}
                <Button type="button" size="sm" variant="ghost" onClick={() => onRecheck(r)}>
                  Check again
                </Button>
              </>
            ),
          },
          { key: "date", header: "Date", numeric: true, className: "whitespace-nowrap", cell: (r) => formatDate(r.audited_on) },
        ]}
        compact={(r) => (
          <CompactRow
            name={`${r.subject}: ${r.field}`}
            symbol={RESULT_LABEL[r.result]}
            primary={formatDate(r.audited_on)}
            secondary={[`Screen ${r.displayed}`, `Source ${r.source_value ?? "none given"}`]}
            onClick={() => onRecheck(r)}
          />
        )}
      />
    </Blueprint>
  );
}
