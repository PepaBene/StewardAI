"use client";

import { useCallback, useState } from "react";

import { ChartFrame, DataStateBadge } from "@/components/data-state";
import { getBrowserApiClient } from "@/lib/api/client";
import { errorMessage } from "@/lib/api/error-message";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { backtestBlocked, LEDGER_TIER_META, LEDGER_TIERS, ledgerCounts, ledgerProvenance, normaliseSubjectKey, overrideNote, subjectKeyProblem } from "@/lib/xray/funds";
import { date } from "@/lib/xray/format";
import type { CoverageLedger, LedgerRow } from "@/lib/xray/types";

import { ConfirmAction, JobTrigger } from "./confirm-action";
import { DataTable } from "./facts";
import { SafeLink } from "./safe-link";
import { Panel, ResourceShell } from "./resource-shell";

/** Fund and index coverage ledger, with owner-confirmed refresh, manual rows and snapshot pins. */
export function CoverageSection() {
  const fetcher = useCallback(() => getBrowserApiClient().GET("/funds/coverage-ledger"), []);
  const resource = useApiResource(fetcher);
  return (
    <div className="flex flex-col gap-5">
      <ResourceShell state={resource.state} label="Coverage ledger">
        {(ledger, lastGood) => <LedgerBody ledger={ledger} lastGood={lastGood} reload={resource.reload} />}
      </ResourceShell>
      <Panel
        id="coverage-jobs"
        kicker="Collection"
        title="Fetch fund holdings"
        description="These run only when you start them. Funds whose stored holdings are already current are skipped."
      >
        <div className="flex flex-wrap gap-4">
          <JobTrigger job="etf-look-through" />
          <JobTrigger job="nport-holdings" />
        </div>
      </Panel>
      <PinPanel />
    </div>
  );
}

function LedgerBody({ ledger, lastGood, reload }: { ledger: CoverageLedger; lastGood: boolean; reload: () => void }) {
  const base = ledgerProvenance(ledger);
  const provenance = lastGood ? { ...base, state: "unavailable" as const, reason: "The refresh failed; this is the last value that loaded." } : base;
  const blocked = backtestBlocked(ledger);
  const counts = ledgerCounts(ledger);
  return (
    <>
      <ChartFrame
        id="coverage-ledger"
        kicker="Coverage ledger"
        title="Where each fund's and index's constituents come from"
        takeaway={
          ledger.entries.length > 0
            ? `${ledger.entries.length - (counts.find((c) => c.tier === "unavailable")?.count ?? 0)} of ${ledger.entries.length} subjects have a constituent source; ${blocked.length} should not feed a performance or backtest without review.`
            : undefined
        }
        units="Counts of ledger subjects (funds and indices). A subject with no row is unknown, not covered or uncovered."
        provenance={provenance}
        hasValue={ledger.entries.length > 0}
        controls={
          <ConfirmAction
            label="Refresh ledger"
            confirmTitle="Rebuild the derived ledger rows?"
            confirmBody="Re-derives the rows from the Jobs configuration and stored snapshots. Rows you recorded yourself are never overwritten."
            confirmLabel="Refresh"
            run={async () => {
              const r = await getBrowserApiClient().POST("/funds/coverage-ledger/refresh");
              if (!r.data) throw new Error(`The refresh failed (HTTP ${r.response.status}${errorMessage(r.error) ? `: ${errorMessage(r.error)}` : ""}).`);
              reload();
              return "Ledger refreshed.";
            }}
          />
        }
      >
        <ul className="flex flex-wrap gap-x-5 gap-y-1 text-xs" aria-label="Subjects per source tier">
          {LEDGER_TIERS.map((t) => (
            <li key={t} className="inline-flex items-center gap-1.5">
              <DataStateBadge state={LEDGER_TIER_META[t].state} reason={LEDGER_TIER_META[t].description} />
              <span>{LEDGER_TIER_META[t].label}</span>
              <span className="num font-semibold">{counts.find((c) => c.tier === t)?.count ?? 0}</span>
            </li>
          ))}
        </ul>
        <DataTable
          caption="Coverage ledger rows"
          minWidth="62rem"
          columns={[
            { label: "Subject" },
            { label: "Source tier" },
            { label: "Holdings as of", align: "right" },
            { label: "Total return" },
            { label: "Historical constituents" },
            { label: "Why" },
            { label: "Backtest" },
          ]}
          rows={ledger.entries.map((e) => [
            <span key="s">
              <span className="font-medium">{e.label}</span>
              <span className="block text-[11px] text-text-muted">
                {e.subject_kind} · {e.subject_key} · {e.origin === "manual" ? "owner-verified" : "derived"}
              </span>
            </span>,
            <span key="t" className="inline-flex flex-col gap-0.5">
              <DataStateBadge state={LEDGER_TIER_META[e.tier].state} reason={LEDGER_TIER_META[e.tier].description} />
              <span className="text-[11px]">{LEDGER_TIER_META[e.tier].label}</span>
              {e.proxy_fund_isin ? <span className="font-mono text-[11px] text-text-muted">proxy {e.proxy_fund_isin}</span> : null}
            </span>,
            date(e.latest_holdings_as_of),
            e.total_return_available === null ? "unknown" : e.total_return_available ? "yes" : "no",
            e.historical_constituents === null ? "unknown" : e.historical_constituents ? "yes" : "no",
            <span key="r" className="text-[11px] text-text-muted">
              {e.reason}
              {e.source_url ? (
                <>
                  {" "}
                  <SafeLink href={e.source_url} className="text-accent-ink hover:underline">
                    source
                  </SafeLink>
                </>
              ) : null}
              <span className="block">Checked {date(e.checked_at)}</span>
            </span>,
            e.usable_for_backtest ? (
              <span key="b" className="text-[11px]">
                usable
              </span>
            ) : (
              <span key="b" className="text-[11px] text-text font-medium">
                flagged: {e.backtest_flags.join("; ") || "review before use"}
              </span>
            ),
          ])}
        />
      </ChartFrame>
      <ManualEntry reload={reload} entries={ledger.entries} />
    </>
  );
}

const ISIN = /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/;

function ManualEntry({ reload, entries }: { reload: () => void; entries: readonly LedgerRow[] }) {
  const [kind, setKind] = useState<"fund" | "index">("fund");
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const [tier, setTier] = useState<"" | keyof typeof LEDGER_TIER_META>("");
  const [reason, setReason] = useState("");
  const [proxy, setProxy] = useState("");
  const [totalReturn, setTotalReturn] = useState<"" | "yes" | "no">("");
  const [historical, setHistorical] = useState<"" | "yes" | "no">("");
  const [source, setSource] = useState("");
  const proxyOk = proxy === "" || ISIN.test(proxy);
  const normKey = normaliseSubjectKey(kind, key, entries);
  const keyProblem = key.trim() === "" ? null : subjectKeyProblem(kind, normKey);
  const existing = entries.find((e) => e.subject_kind === kind && e.subject_key === normKey);
  const override = overrideNote(existing);
  const valid = normKey !== "" && keyProblem === null && label.trim() !== "" && tier !== "" && reason.trim().length >= 3 && proxyOk;
  const tri = (v: "" | "yes" | "no") => (v === "" ? null : v === "yes");
  return (
    <Panel
      id="coverage-manual"
      kicker="Owner-verified row"
      title="Record a ledger row"
      description="Use this when you have checked a fund's or index's source yourself. A later refresh never overwrites it. Saving over an existing derived row replaces it for good: there is no revert from this screen (the API has no delete)."
    >
      <details className="border border-border-soft">
        <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-accent-ink select-none hover:underline">Open the form</summary>
        <form
          className="grid grid-cols-1 gap-3 border-t border-border-soft p-3 sm:grid-cols-2"
          onSubmit={(e) => e.preventDefault()}
        >
          <Field label="Subject kind">
            <select value={kind} onChange={(e) => setKind(e.target.value as "fund" | "index")} className={INPUT}>
              <option value="fund">Fund</option>
              <option value="index">Index</option>
            </select>
          </Field>
          <Field label="Subject key (ISIN or index symbol)">
            <input value={key} maxLength={80} onChange={(e) => setKey(e.target.value)} aria-invalid={keyProblem !== null} className={INPUT} />
            {keyProblem ? <span className="text-text">{keyProblem}</span> : normKey !== key.trim() ? <span>Saved as {normKey}.</span> : null}
          </Field>
          <Field label="Label">
            <input value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} className={INPUT} />
          </Field>
          <Field label="Source tier">
            <select value={tier} onChange={(e) => setTier(e.target.value as keyof typeof LEDGER_TIER_META)} className={INPUT}>
              <option value="">Choose a tier…</option>
              {LEDGER_TIERS.map((t) => (
                <option key={t} value={t}>
                  {LEDGER_TIER_META[t].label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Reason (at least 3 characters)" wide>
            <textarea value={reason} maxLength={1000} rows={2} onChange={(e) => setReason(e.target.value)} className={`${INPUT} h-auto py-1.5`} />
          </Field>
          <Field label="Proxy fund ISIN (optional)">
            <input value={proxy} onChange={(e) => setProxy(e.target.value.toUpperCase())} aria-invalid={!proxyOk} className={INPUT} />
          </Field>
          <Field label="Source URL (optional)">
            <input value={source} maxLength={2000} onChange={(e) => setSource(e.target.value)} className={INPUT} />
          </Field>
          <Field label="Total return available">
            <select value={totalReturn} onChange={(e) => setTotalReturn(e.target.value as "" | "yes" | "no")} className={INPUT}>
              <option value="">Unknown</option>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          </Field>
          <Field label="Historical constituents available">
            <select value={historical} onChange={(e) => setHistorical(e.target.value as "" | "yes" | "no")} className={INPUT}>
              <option value="">Unknown</option>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          </Field>
          <div className="sm:col-span-2">
            <ConfirmAction
              label="Review and save"
              variant="default"
              confirmTitle={`Save the ledger row for ${label || key || "this subject"}?`}
              confirmBody={
                <>
                  This records an owner-verified row under <span className="font-mono">{normKey || "the key above"}</span> and keeps it through refreshes.{" "}
                  {override ?? "No row exists for this key yet, so a new row is created."}
                </>
              }
              confirmLabel="Save row"
              run={async () => {
                if (!valid) throw new Error(keyProblem ?? "Fill in the key, label, a source tier, a reason of at least 3 characters and a valid proxy ISIN.");
                const r = await getBrowserApiClient().PUT("/funds/coverage-ledger", {
                  body: {
                    subject_kind: kind,
                    subject_key: normKey,
                    label: label.trim(),
                    tier: tier as keyof typeof LEDGER_TIER_META,
                    reason: reason.trim(),
                    proxy_fund_isin: proxy || null,
                    total_return_available: tri(totalReturn),
                    historical_constituents: tri(historical),
                    source_url: source.trim() || null,
                  },
                });
                if (!r.data) throw new Error(`The row could not be saved (HTTP ${r.response.status}${errorMessage(r.error) ? `: ${errorMessage(r.error)}` : ""}).`);
                reload();
                return "Row saved.";
              }}
            />
            {!valid ? <p className="mt-1 text-xs text-text-muted">Complete the required fields to enable a valid save.</p> : null}
          </div>
        </form>
      </details>
    </Panel>
  );
}

function PinPanel() {
  const [kind, setKind] = useState<"report" | "decision" | "manual">("manual");
  const [refId, setRefId] = useState("");
  const [asOf, setAsOf] = useState("");
  const [reason, setReason] = useState("");
  const valid = refId.trim() !== "" && asOf !== "";
  return (
    <Panel
      id="coverage-pin"
      kicker="Retention"
      title="Pin the holdings a report rests on"
      description="Pinned snapshots are protected from retention pruning, so a saved report or decision can always be re-derived from the same fund holdings."
    >
      <details className="border border-border-soft">
        <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-accent-ink select-none hover:underline">Open the form</summary>
        <form className="grid grid-cols-1 gap-3 border-t border-border-soft p-3 sm:grid-cols-2" onSubmit={(e) => e.preventDefault()}>
          <Field label="Pinned for">
            <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)} className={INPUT}>
              <option value="manual">Manual</option>
              <option value="report">A report</option>
              <option value="decision">A decision</option>
            </select>
          </Field>
          <Field label="Reference (report id, decision id or note key)">
            <input value={refId} maxLength={200} onChange={(e) => setRefId(e.target.value)} className={INPUT} />
          </Field>
          <Field label="Every configured fund's snapshot known on">
            <input type="date" value={asOf} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setAsOf(e.target.value)} className={INPUT} />
          </Field>
          <Field label="Reason (optional)">
            <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} className={INPUT} />
          </Field>
          <div className="sm:col-span-2">
            <ConfirmAction
              label="Review and pin"
              variant="default"
              confirmTitle={`Pin the snapshots known on ${asOf || "the chosen date"}?`}
              confirmBody="Each configured fund's snapshot known by the end of that day is protected from pruning. Pins are not removed from this screen."
              confirmLabel="Pin snapshots"
              run={async () => {
                if (!valid) throw new Error("Choose a date and enter a reference.");
                const r = await getBrowserApiClient().POST("/funds/snapshots/pin", {
                  body: { ref_kind: kind, ref_id: refId.trim(), as_of: asOf, reason: reason.trim() || null },
                });
                if (!r.data) throw new Error(`The snapshots could not be pinned (HTTP ${r.response.status}${errorMessage(r.error) ? `: ${errorMessage(r.error)}` : ""}).`);
                const n = (r.data as { pinned?: number[] }).pinned?.length ?? 0;
                return n === 0 ? "No snapshot was known on that date, so nothing was pinned." : `Pinned ${n} snapshot${n === 1 ? "" : "s"}.`;
              }}
            />
          </div>
        </form>
      </details>
    </Panel>
  );
}

const INPUT =
  "h-9 w-full min-w-0 border border-border bg-surface-raised px-2 text-sm text-text outline-none focus-visible:border-focus focus-visible:ring-2 focus-visible:ring-focus/30 aria-invalid:border-warning-signal";

function Field({ label, wide, children }: { label: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <label className={wide ? "flex flex-col gap-1 text-xs text-text-muted sm:col-span-2" : "flex flex-col gap-1 text-xs text-text-muted"}>
      <span>{label}</span>
      {children}
    </label>
  );
}

