"use client";

import { AlertTriangleIcon, BanIcon, CheckCircle2Icon, CircleDashedIcon, ClockIcon, ExternalLinkIcon, HelpCircleIcon, LockIcon, SearchXIcon } from "lucide-react";
import { useState } from "react";

import { callApi, DecisionForm } from "@/app/(app)/relationships/decision-form";
import { getBrowserApiClient } from "@/lib/api/client";
import { metricLabel } from "@/lib/evidence/confidence";
import {
  COVERAGE_STATES,
  coverageInfo,
  dateRows,
  FAMILY_LABEL,
  filingStatus,
  formatDay,
  formatStamp,
  formatValue,
  formFamily,
  TONE_CLASS,
  type Claim,
  type Coverage,
  type CoverageState,
  type Filing,
  type Observation,
  type Provenance,
  type ReviewRow,
  type SourceRow,
  type SpanText,
} from "@/lib/disclosure/model";
import { safeHref } from "@/lib/reference/identity";
import { cn } from "@/lib/utils";

const STATE_ICON: Record<CoverageState, typeof CheckCircle2Icon> = {
  verified: CheckCircle2Icon,
  under_review: ClockIcon,
  not_disclosed: CircleDashedIcon,
  not_covered: SearchXIcon,
  paid_only: LockIcon,
  authentication_failed: BanIcon,
  retrieval_failed: AlertTriangleIcon,
  parse_failed: AlertTriangleIcon,
  unresolved_identity: HelpCircleIcon,
  stale: ClockIcon,
};

/** Icon, label and tone together: a state never relies on colour alone. */
export function StateBadge({ state }: { state: string }) {
  const info = coverageInfo(state);
  const Icon = STATE_ICON[state as CoverageState] ?? HelpCircleIcon;
  return (
    <span title={info.help} className={cn("inline-flex items-center gap-1.5 border px-2 py-0.5 text-xs font-medium whitespace-nowrap", TONE_CLASS[info.tone])}>
      <Icon aria-hidden="true" className="size-3.5" />
      {info.label}
    </span>
  );
}

// ------------------------------------------------------------------------------------- coverage
export function CoverageRibbon({ coverage }: { coverage: readonly Coverage[] }) {
  if (coverage.length === 0) {
    return <p className="text-sm text-text-muted">No coverage has been recorded for this issuer yet. That means unchecked, not complete.</p>;
  }
  const counts = new Map<string, number>();
  for (const c of coverage) counts.set(c.state, (counts.get(c.state) ?? 0) + 1);
  const order = Object.keys(COVERAGE_STATES);
  return (
    <div className="space-y-3">
      <ul className="flex flex-wrap gap-2" aria-label="Coverage summary">
        {order
          .filter((s) => counts.has(s))
          .map((s) => (
            <li key={s} className="flex items-center gap-1.5">
              <StateBadge state={s} />
              <span className="num text-sm">{counts.get(s)}</span>
            </li>
          ))}
      </ul>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] border-collapse text-sm">
          <caption className="sr-only">Coverage by metric and period, with the reason for each state</caption>
          <thead>
            <tr className="border-b border-border text-left">
              <th scope="col" className="py-1.5 pr-3 font-medium">Metric</th>
              <th scope="col" className="py-1.5 pr-3 font-medium">Period</th>
              <th scope="col" className="py-1.5 pr-3 font-medium">State</th>
              <th scope="col" className="py-1.5 pr-3 font-medium">Why</th>
              <th scope="col" className="py-1.5 font-medium">Checked</th>
            </tr>
          </thead>
          <tbody>
            {coverage.map((c, i) => (
              <tr key={`${c.metric}-${c.period}-${i}`} className="border-b border-border-soft align-top">
                <td className="py-1.5 pr-3">{metricLabel(c.metric)}</td>
                <td className="num py-1.5 pr-3">{c.period}</td>
                <td className="py-1.5 pr-3"><StateBadge state={c.state} /></td>
                <td className="py-1.5 pr-3 text-text-muted">{c.reason || coverageInfo(c.state).help}</td>
                <td className="num py-1.5 whitespace-nowrap text-text-muted">{formatDay(c.checked_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------------------------- filings
const STATUS_LABEL = { current: "Current", superseded: "Replaced", amendment: "Amendment", quarantined: "Could not be read" } as const;

export function FilingsTable({ filings: all, selectedId, onSelect }: { filings: readonly Filing[]; selectedId: number | null; onSelect: (id: number) => void }) {
  // Earlier reads of a filing the same document later replaced are kept for the replay, but
  // listed only on request: five rows of one 8-K read five times say nothing new.
  const [showReplaced, setShowReplaced] = useState(false);
  if (all.length === 0) {
    return <p className="text-sm text-text-muted">Nothing had been published for this issuer by the chosen date.</p>;
  }
  const replaced = all.filter((f) => f.superseded_by_id != null && f.version_id !== selectedId).length;
  const filings = showReplaced ? all : all.filter((f) => f.superseded_by_id == null || f.version_id === selectedId);
  return (
    <div className="space-y-2 overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <caption className="sr-only">Filings known at the chosen date, newest first</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Document</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Period</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Known from</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Status</th>
            <th scope="col" className="py-1.5 font-medium"><span className="sr-only">Open</span></th>
          </tr>
        </thead>
        <tbody>
          {filings.map((f) => (
            <tr key={f.version_id} className={cn("border-b border-border-soft align-top", f.version_id === selectedId && "bg-accent-soft")}>
              <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                {f.form ?? "Document"}
                <span className="block text-xs font-normal text-text-muted">{FAMILY_LABEL[formFamily(f.form)]}</span>
              </th>
              <td className="num py-1.5 pr-3">{f.fiscal_period ?? "—"}</td>
              <td className="num py-1.5 pr-3 whitespace-nowrap">{formatStamp(f.known_at)}</td>
              <td className="py-1.5 pr-3">
                {filingStatus(f) === "quarantined" ? <StateBadge state="parse_failed" /> : STATUS_LABEL[filingStatus(f)]}
                {f.amends_version_id != null ? <span className="block text-xs text-text-muted">restates an earlier filing</span> : null}
                {f.superseded_by_id != null ? <span className="block text-xs text-text-muted">a later read of the same filing replaced it</span> : null}
              </td>
              <td className="py-1.5 text-right">
                <button type="button" onClick={() => onSelect(f.version_id)} className="text-accent-ink hover:underline focus-visible:outline-2 focus-visible:outline-focus">
                  Dates<span className="sr-only"> for {f.form}</span>
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {replaced > 0 ? (
        <button type="button" onClick={() => setShowReplaced((v) => !v)} className="text-xs text-accent-ink hover:underline">
          {showReplaced ? "Hide earlier reads" : `Show ${replaced} earlier ${replaced === 1 ? "read" : "reads"} of these filings`}
        </button>
      ) : null}
    </div>
  );
}

export function DatesTable({ filing }: { filing: Filing }) {
  return (
    <div className="space-y-2">
      <h3 className="text-base font-semibold">
        {filing.form} {filing.fiscal_period ?? ""} — six dates
      </h3>
      {filing.quarantined ? (
        <p role="note" className="border border-warning-signal/40 bg-warning-signal-subtle px-3 py-2 text-sm">
          <AlertTriangleIcon aria-hidden="true" className="mr-1.5 inline size-4 align-[-3px]" />
          This document could not be read{filing.quarantine_reason ? `: ${filing.quarantine_reason}` : "."} It is kept for review and produces no values.
        </p>
      ) : null}
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
        {dateRows(filing).map((r) => (
          <div key={r.key} className="contents">
            <dt className="font-medium">{r.label}</dt>
            <dd>
              {r.value ? <span className="num">{formatStamp(r.value)}</span> : <span className="text-text-muted">Not given. {r.note}</span>}
              {r.value && r.key === "known_at" ? <span className="block text-xs text-text-muted">{r.note}</span> : null}
            </dd>
          </div>
        ))}
      </dl>
      <a href={safeHref(filing.stable_url) ?? undefined} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-accent-ink hover:underline">
        Open the original filing <ExternalLinkIcon aria-hidden="true" className="size-3.5" />
      </a>
    </div>
  );
}

// ------------------------------------------------------------------------------------ values
const VALUES_SHOWN = 15;

export function ValuesTable({ observations: allObservations, claims, onEvidence }: { observations: readonly Observation[]; claims: readonly Claim[]; onEvidence: (kind: "observation" | "claim", id: number) => void }) {
  const [all, setAll] = useState(false);
  if (allObservations.length === 0 && claims.length === 0) {
    return <p className="text-sm text-text-muted">No values or relationships had been read by the chosen date.</p>;
  }
  // The headline line items (revenue, net income...) before the long tail of raw XBRL concepts,
  // each newest period first; the rest on request.
  const ordered = [...allObservations].sort(
    (a, b) => Number(a.metric.startsWith("concept:")) - Number(b.metric.startsWith("concept:")) || (b.period_end ?? "").localeCompare(a.period_end ?? ""),
  );
  const observations = all ? ordered : ordered.slice(0, VALUES_SHOWN);
  const hidden = ordered.length - observations.length;
  return (
    <div className="space-y-2 overflow-x-auto">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <caption className="sr-only">Values and relationships known at the chosen date</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Item</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Period</th>
            <th scope="col" className="py-1.5 pr-3 text-right font-medium">Value</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Review</th>
            <th scope="col" className="py-1.5 font-medium"><span className="sr-only">Evidence</span></th>
          </tr>
        </thead>
        <tbody>
          {observations.map((o) => (
            <tr key={`o${o.id}`} className="border-b border-border-soft align-top">
              <th scope="row" className="py-1.5 pr-3 text-left font-medium" title={o.metric}>
                {metricLabel(o.metric)}
                {o.segment ? <span className="block text-xs font-normal text-text-muted">{o.segment}</span> : null}
              </th>
              <td className="num py-1.5 pr-3 whitespace-nowrap">{o.period_end ?? "—"}</td>
              <td className={cn("num py-1.5 pr-3 text-right", o.value == null && "text-text-muted")}>{formatValue(o)}</td>
              <td className="py-1.5 pr-3"><StateBadge state={o.review_state === "verified" ? "verified" : "under_review"} /></td>
              <td className="py-1.5 text-right">
                <button type="button" onClick={() => onEvidence("observation", o.id)} className="text-accent-ink hover:underline">
                  Evidence<span className="sr-only"> for {o.metric}</span>
                </button>
              </td>
            </tr>
          ))}
          {claims.map((c) => (
            <tr key={`c${c.id}`} className="border-b border-border-soft align-top">
              <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                {c.claim_type.replace("relationship.", "")} relationship
                <span className="block text-xs font-normal text-text-muted">{c.object_anonymous ? "Counterparty not named" : (c.object_label ?? "Unmatched company")}</span>
              </th>
              <td className="num py-1.5 pr-3 whitespace-nowrap">{c.valid_from ?? "—"} to {c.valid_to ?? "—"}</td>
              <td className="py-1.5 pr-3 text-right text-text-muted">Suggestion, confidence {c.confidence != null ? Math.round(c.confidence * 100) : "?"}%</td>
              <td className="py-1.5 pr-3"><StateBadge state={c.review_state === "verified" ? "verified" : "under_review"} /></td>
              <td className="py-1.5 text-right">
                <button type="button" onClick={() => onEvidence("claim", c.id)} className="text-accent-ink hover:underline">
                  Evidence<span className="sr-only"> for relationship</span>
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {hidden > 0 || all ? (
        <button type="button" onClick={() => setAll((v) => !v)} className="text-xs text-accent-ink hover:underline">
          {all ? "Show the main line items only" : `Show ${hidden} more values`}
        </button>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------------- evidence
/** The passage in the filing's own words, the exact characters the value came from marked. */
export function EvidencePanel({ provenance, span, onClose }: { provenance: Provenance; span: SpanText | null; onClose: () => void }) {
  return (
    <div className="space-y-3 border border-border bg-surface-muted p-4" role="region" aria-label="Evidence">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold">
            {provenance.form} {provenance.fiscal_period ?? ""}
          </h3>
          <p className="text-sm text-text-muted">
            {provenance.evidence.section ?? "Section not recorded"}
            {provenance.table_name ? ` · ${provenance.table_name}${provenance.row_ref ? `, ${provenance.row_ref}` : ""}` : ""}
          </p>
        </div>
        <button type="button" onClick={onClose} className="text-sm text-accent-ink hover:underline">Close evidence</button>
      </div>

      {span ? (
        <blockquote className="max-w-[72ch] border-l-2 border-accent pl-4 font-serif text-[17px] leading-relaxed whitespace-pre-wrap">
          {span.before}
          <mark className="bg-accent-soft px-0.5 text-text underline decoration-accent decoration-2 underline-offset-2">{span.span}</mark>
          {span.after}
        </blockquote>
      ) : provenance.evidence.quote ? (
        <blockquote className="max-w-[72ch] border-l-2 border-accent pl-4 font-serif text-[17px] leading-relaxed">{provenance.evidence.quote}</blockquote>
      ) : (
        <p className="text-sm text-text-muted">The passage text is not available.</p>
      )}
      {span?.stale_parser ? (
        <p role="note" className="text-sm">
          <AlertTriangleIcon aria-hidden="true" className="mr-1 inline size-4 align-[-3px]" />
          The reader has changed since this was extracted (now {span.parser_version}). Character positions may have moved, so the text above comes from the stored quote.
        </p>
      ) : null}

      <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[10rem_1fr]">
        <dt className="font-medium">How it was read</dt>
        <dd>{provenance.extraction_method ?? "Not recorded"}{provenance.confidence != null && provenance.confidence < 1 ? `, confidence ${Math.round(provenance.confidence * 100)}%` : ""}</dd>
        <dt className="font-medium">Review</dt>
        <dd><StateBadge state={provenance.review_state === "verified" ? "verified" : "under_review"} /></dd>
        <dt className="font-medium">Filing id</dt>
        <dd className="num break-all">{provenance.external_id}</dd>
        <dt className="font-medium">Content hash</dt>
        <dd className="num break-all text-text-muted">{provenance.content_hash || "Not recorded"}</dd>
      </dl>

      <div>
        <h4 className="text-sm font-semibold">Versions of this filing</h4>
        <ol className="mt-1 space-y-1 text-sm">
          {provenance.version_chain.map((v) => (
            <li key={v.id} className={cn(v.id === provenance.document_version_id && "font-medium")}>
              <span className="num">{formatStamp(v.known_at)}</span>
              {v.id === provenance.document_version_id ? " — this evidence" : ""}
              {v.superseded_by_id != null ? " — replaced later" : v.is_current ? " — current" : ""}
            </li>
          ))}
        </ol>
      </div>
      <a href={safeHref(provenance.stable_url) ?? undefined} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-accent-ink hover:underline">
        Open the original filing <ExternalLinkIcon aria-hidden="true" className="size-3.5" />
      </a>
    </div>
  );
}

// ----------------------------------------------------------------------------------- sources
const ACCESS_LABEL: Record<string, { text: string; tone: "verified" | "neutral" | "warning" }> = {
  passed: { text: "Access tested", tone: "verified" },
  untested: { text: "Not yet tested", tone: "neutral" },
  failed: { text: "Access failed", tone: "warning" },
  paid_only: { text: "Paid only", tone: "neutral" },
  authentication_failed: { text: "Access refused", tone: "warning" },
};

export function SourcesPanel({ sources }: { sources: readonly SourceRow[] }) {
  if (sources.length === 0) return <p className="text-sm text-text-muted">No sources are registered.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-sm">
        <caption className="sr-only">Public disclosure sources, the result of the last access test, and whether each is switched on</caption>
        <thead>
          <tr className="border-b border-border text-left">
            <th scope="col" className="py-1.5 pr-3 font-medium">Source</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Regulator</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Access</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">Collecting</th>
            <th scope="col" className="py-1.5 font-medium">Stop if</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s) => {
            const a = ACCESS_LABEL[s.access_state] ?? { text: s.access_state, tone: "neutral" as const };
            return (
              <tr key={s.source_key} className="border-b border-border-soft align-top">
                <th scope="row" className="py-1.5 pr-3 text-left font-medium">
                  {s.source_key}
                  <span className="block text-xs font-normal text-text-muted">{s.jurisdiction} · {s.access_method}</span>
                </th>
                <td className="py-1.5 pr-3">{s.regulator}</td>
                <td className="py-1.5 pr-3">
                  <span className={cn("inline-block border px-2 py-0.5 text-xs font-medium", TONE_CLASS[a.tone])}>{a.text}</span>
                  <span className="num block text-xs text-text-muted">{s.tested_on ? `tested ${formatDay(s.tested_on)}` : "never tested"}</span>
                  {s.access_detail ? <span className="block max-w-[34ch] text-xs text-text-muted">{s.access_detail}</span> : null}
                </td>
                <td className="py-1.5 pr-3">{s.enabled ? "On" : "Off"}</td>
                <td className="py-1.5 text-text-muted">{s.stop_condition}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------------------------ review
const DECISIONS = [
  { value: "approved", label: "Approve" },
  { value: "rejected", label: "Reject" },
  { value: "superseded", label: "Mark as replaced" },
  { value: "deferred", label: "Decide later" },
] as const;

const REASON_LABEL: Record<string, string> = {
  entity_ambiguity: "Company could not be matched",
  parse_failure: "Document could not be read",
};

export function ReviewPanel({ reviews, sample, onDecided }: { reviews: readonly ReviewRow[]; sample: boolean; onDecided: () => void }) {
  if (reviews.length === 0) return <p className="text-sm text-text-muted">Nothing is waiting for review.</p>;
  return (
    <ul className="space-y-3">
      {reviews.map((r) => (
        <li key={r.id} className="space-y-2 border border-border bg-surface-raised p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-medium">{REASON_LABEL[r.reason] ?? r.reason}</p>
            <span className="num text-xs text-text-muted">opened {formatDay(r.created_at)}</span>
          </div>
          <pre className="max-w-full overflow-x-auto bg-surface-muted p-2 text-xs whitespace-pre-wrap">{JSON.stringify(r.candidate, null, 2)}</pre>
          {r.status === "open" ? (
            sample ? (
              <p className="text-xs text-text-muted">Sample data: decisions are switched off.</p>
            ) : (
              <DecisionForm
                options={DECISIONS}
                summary="Record a decision"
                onSubmit={async (decision, reason) => {
                  const res = await callApi(
                    () =>
                      getBrowserApiClient().POST("/disclosures/review/{review_id}/decision", {
                        params: { path: { review_id: r.id } },
                        body: { decision: decision as (typeof DECISIONS)[number]["value"], reason },
                      }),
                    "Decision recorded.",
                  );
                  if (res.ok) onDecided();
                  return res;
                }}
              />
            )
          ) : (
            <p className="text-sm text-text-muted">
              {r.status} by {r.decided_by ?? "unknown"} on {formatDay(r.decided_at)}: {r.decision_reason}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}
