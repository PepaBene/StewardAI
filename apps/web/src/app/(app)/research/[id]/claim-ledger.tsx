import { ShieldAlertIcon } from "lucide-react";

import type { ResearchBody } from "@/lib/api/client";
import {
  balanceLines,
  claimTypeLabel,
  COMPLIANCE_LABELS,
  complianceLine,
  complianceOf,
  confidenceLabel,
  coverageNotices,
  flaggedSourceWarnings,
  groupClaimsBySection,
  staleClaimCount,
  strategyKindLabel,
  strategyStatusLabel,
  tally,
} from "@/lib/research/ledger";

import { LedgerClaimRow } from "./ledger-claim-row";
import { NoteList, Panel, Tag } from "./report-bits";

/** The claim ledger: report confidence, notices, strategy checks, balance, flagged sources and every typed claim. */
export function ClaimLedger({ reportId, body }: { reportId: number; body: ResearchBody }) {
  const { ledger } = body;
  const flagged = flaggedSourceWarnings(body.flagged_sources, body.sources);
  if (!ledger) {
    return flagged.length > 0 ? <FlaggedSources flagged={flagged} /> : null;
  }
  const groups = groupClaimsBySection(ledger.claims, body.sections);
  const stale = staleClaimCount(ledger.claims);
  const notices = coverageNotices(ledger.coverage);
  const balance = balanceLines(ledger.balance);
  return (
    <Panel
      id="claim-ledger"
      title={`Claim ledger · ${ledger.claims.length}`}
      hint="Each sentence typed, with the confidence it deserves and the reason. Your review is kept beside the sentence and never changes it."
    >
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <b className="font-semibold">Report confidence: {confidenceLabel(ledger.confidence)}</b>
        <span className="text-text-muted">{ledger.confidence_why}</span>
      </p>
      <p className="flex flex-wrap gap-1.5 text-xs">
        {tally(ledger.by_type, claimTypeLabel).map((t) => (
          <Tag key={`t-${t.key}`}>
            {t.label} <span className="num">{t.count}</span>
          </Tag>
        ))}
        {tally(ledger.by_confidence, confidenceLabel).map((t) => (
          <Tag key={`c-${t.key}`}>
            {t.label} <span className="num">{t.count}</span>
          </Tag>
        ))}
        {stale > 0 ? (
          <Tag className="border-warning-signal/40 bg-warning-signal-subtle text-text">
            Stale <span className="num">{stale}</span>
          </Tag>
        ) : null}
      </p>

      {notices.length > 0 ? (
        <div>
          <h3 className="text-xs font-medium text-text-muted">As of and coverage</h3>
          <p className="num text-xs text-text-muted">Ledger built {ledger.coverage.as_of}</p>
          <NoteList lines={notices} />
        </div>
      ) : null}

      {ledger.checks.length > 0 ? (
        <div>
          <h3 className="text-xs font-medium text-text-muted">Checks</h3>
          <ul className="space-y-0.5 text-sm">
            {ledger.checks.map((c) => (
              <li key={`${c.code}-${c.message}`}>
                <Tag className="mr-1.5">{c.severity}</Tag>
                {c.message}
                {c.claims && c.claims.length > 0 ? <span className="text-xs text-text-muted"> ({c.claims.join(", ")})</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {ledger.strategy.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[28rem] text-xs">
            <caption className="pb-1 text-left text-text-muted">
              Strategy and thesis checks. A formal check comes from a stored verdict; the others read the report&apos;s text.
            </caption>
            <thead>
              <tr className="border-b border-border text-left text-[11px] tracking-wide text-text-muted uppercase">
                <th scope="col" className="py-1 pr-2 font-medium">Rule</th>
                <th scope="col" className="py-1 pr-2 font-medium">Status</th>
                <th scope="col" className="py-1 font-medium">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {ledger.strategy.map((s) => (
                <tr key={`${s.kind}-${s.id}`} className="border-b border-border-soft align-top last:border-0">
                  <th scope="row" className="py-1 pr-2 text-left font-normal">
                    {s.rule}
                    <span className="block text-text-muted">
                      {strategyKindLabel(s.kind)}
                      {s.version !== null ? ` · config v${s.version}` : ""}
                    </span>
                  </th>
                  <td className="py-1 pr-2">
                    {complianceOf(s) ? COMPLIANCE_LABELS[complianceOf(s)!.status] : strategyStatusLabel(s.status)}
                    <span className="block text-text-muted">
                      {complianceOf(s) ? "rule engine check" : s.formal ? "formal check" : "from the text"}
                    </span>
                  </td>
                  <td className="py-1">
                    {complianceOf(s) ? (
                      <>
                        {complianceLine(complianceOf(s)!)}
                        {complianceOf(s)!.status !== "unknown" && complianceOf(s)!.status !== "pass" ? (
                          <span className="block text-text-muted">{complianceOf(s)!.reason}</span>
                        ) : null}
                      </>
                    ) : (
                      (s.evidence ?? <span className="text-text-muted">none</span>)
                    )}
                    {(s.named_by?.length ?? 0) > 0 ? (
                      <span className="block text-text-muted">Named in {s.named_by?.length} claim{s.named_by?.length === 1 ? "" : "s"} of the report (its reading, kept apart from the check)</span>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {balance.length > 0 ? (
        <div>
          <h3 className="text-xs font-medium text-text-muted">Balance of the argument</h3>
          <NoteList lines={balance} />
        </div>
      ) : null}

      {flagged.length > 0 ? <FlaggedSources flagged={flagged} /> : null}

      <div>
        <h3 className="mb-1 text-xs font-medium text-text-muted">Claims</h3>
        {groups.map((g) => (
          <details key={g.sectionId} className="border-t border-border-soft pt-1">
            <summary className="cursor-pointer py-1 text-sm font-medium">
              {g.title} · <span className="num">{g.claims.length}</span>
            </summary>
            <ul>
              {g.claims.map((c) => (
                <LedgerClaimRow key={c.id} reportId={reportId} claim={c} />
              ))}
            </ul>
          </details>
        ))}
      </div>
    </Panel>
  );
}

/** Sources with instruction-like text: kept as data, flagged so the owner reads them with care. */
function FlaggedSources({ flagged }: { flagged: { id: string; title: string }[] }) {
  return (
    <p role="alert" className="flex items-start gap-1.5 border border-warning-signal/40 bg-warning-signal-subtle p-2 text-xs text-text">
      <ShieldAlertIcon aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-warning-signal" />
      <span>Flagged sources (instruction-like text, treated as data only): {flagged.map((f) => `${f.id} ${f.title}`).join("; ")}.</span>
    </p>
  );
}
