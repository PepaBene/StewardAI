"use client";

import { ArrowRightIcon, RotateCcwIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { DataStateBadge } from "@/components/data-state";
import { Button } from "@/components/ui/button";
import { DATA_STATES } from "@/lib/data-state/states";
import { formatDate, formatDateTime, formatRelative } from "@/lib/format";
import { accessCounts, describeCell, drillThroughHref, type FailedJob } from "@/lib/source-coverage/logic";
import { ENTITY_SCOPES, type AccessRow, type EntityScope, type MatrixCell, type ProviderGroup } from "@/lib/source-coverage/types";
import { cn } from "@/lib/utils";

const SCOPE_LABEL: Record<EntityScope, string> = {
  held: "Held",
  watchlist: "Watchlist",
  other: "Other entities",
};
const SCOPE_HINT: Record<EntityScope, string> = {
  held: "Securities you hold",
  watchlist: "Securities on the watchlist",
  other: "Countries, peers, benchmarks and keys that match no held or watchlist ticker",
};

/** The reset-block endpoint accepts only these path segments. */
const PATH_SEGMENT = /^[a-z0-9_]{2,40}$/;

function Cell({ cell, now }: { cell: MatrixCell; now: Date }) {
  if (cell.state === null) {
    return (
      <div className="flex flex-col gap-0.5 text-xs text-text-muted">
        <span className="font-medium text-text-muted">No ledger entry</span>
        <span>Unknown, not covered and not zero.</span>
      </div>
    );
  }
  const gaps = cell.gapKeys;
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <DataStateBadge state={cell.state} reason={cell.reasons[0] ?? null} />
      <span className="num text-xs text-text-muted">{describeCell(cell)}</span>
      {cell.expectedPeriods !== null && cell.availablePeriods !== null ? (
        <span className="num text-xs text-text-muted">
          {cell.availablePeriods} of {cell.expectedPeriods} expected periods
        </span>
      ) : null}
      <span className="num text-xs text-text-muted">
        {cell.latestPeriod ? (
          <>
            Latest period <time dateTime={cell.latestPeriod}>{formatDate(cell.latestPeriod)}</time>
            {cell.maxAgeDays !== null ? ` (up to ${cell.maxAgeDays} d old)` : ""}
          </>
        ) : (
          "No period reported"
        )}
      </span>
      <span className="num text-xs text-text-muted">
        {cell.lastVerifiedAt ? (
          <>
            Last verified <time dateTime={cell.lastVerifiedAt} title={formatDateTime(cell.lastVerifiedAt)}>{formatRelative(new Date(cell.lastVerifiedAt), now)}</time>
          </>
        ) : (
          "No verified value"
        )}
        {cell.lastCheckedAt && cell.lastCheckedAt !== cell.lastVerifiedAt ? (
          <>
            {" · "}checked <time dateTime={cell.lastCheckedAt} title={formatDateTime(cell.lastCheckedAt)}>{formatRelative(new Date(cell.lastCheckedAt), now)}</time>
          </>
        ) : null}
      </span>
      {cell.refreshFailing ? (
        <span className="inline-flex items-start gap-1 text-xs text-text">
          <RotateCcwIcon aria-hidden="true" className="mt-0.5 size-3 shrink-0 text-warning-signal" />
          Refresh failing: the last verified value is kept
        </span>
      ) : null}
      {cell.reasons.map((r) => (
        <span key={r} className="text-xs break-words text-text-muted">
          {r}
        </span>
      ))}
      {gaps.length > 0 ? (
        <span className="num text-xs break-words text-text-muted">
          Not available: {gaps.slice(0, 4).join(", ")}
          {gaps.length > 4 ? ` and ${gaps.length - 4} more` : ""}
        </span>
      ) : null}
    </div>
  );
}

/** Two-step owner action: lift a hard block once the credential or plan was fixed. */
export function ResetBlock({ row, onReset }: { row: AccessRow; onReset: (provider: string, endpoint: string) => Promise<string | null> }) {
  const [step, setStep] = useState<"idle" | "confirm" | "busy" | "done">("idle");
  const [error, setError] = useState<string | null>(null);
  // Arming replaces the trigger with the confirm group: move focus to the safe choice (Cancel)
  // and, when it closes again, back to the trigger, so keyboard focus is never dropped on the body.
  const triggerRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const doneRef = useRef<HTMLSpanElement>(null);
  const previous = useRef(step);
  useEffect(() => {
    if (previous.current !== step) {
      if (step === "confirm") cancelRef.current?.focus();
      else if (step === "idle") triggerRef.current?.focus();
      // The confirm group and the trigger are both gone once the block is lifted: the result
      // line takes focus, so keyboard focus is not dropped on <body> and the result is announced.
      else if (step === "done") doneRef.current?.focus();
      previous.current = step;
    }
  }, [step]);
  if (!row.hardBlocked || !PATH_SEGMENT.test(row.provider) || !PATH_SEGMENT.test(row.endpoint)) return null;
  const paid = row.rawState === "paid_only";
  async function run() {
    setStep("busy");
    const failure = await onReset(row.provider, row.endpoint);
    setError(failure);
    setStep(failure ? "idle" : "done");
  }
  if (step === "done") {
    return (
      <span ref={doneRef} role="status" tabIndex={-1} className="text-xs text-text outline-offset-2">
        Block lifted; reload to see the new state.
      </span>
    );
  }
  return (
    <span className="flex flex-col items-start gap-1">
      {step === "confirm" || step === "busy" ? (
        <span role="group" aria-label={`Confirm lifting the block on ${row.provider} ${row.endpoint}`} className="flex flex-col gap-1 border border-border p-2 text-xs">
          <span className="max-w-prose">
            Lift the hard block on {row.provider}/{row.endpoint}? The next job may call it again within its free allowance.
            {paid ? " It was blocked after a paid-tier denial, so it will most likely be denied again and re-blocked." : ""}
          </span>
          <span className="flex gap-2">
            <Button size="sm" variant="outline" disabled={step === "busy"} onClick={run}>
              {step === "busy" ? "Lifting…" : "Lift block"}
            </Button>
            <Button ref={cancelRef} size="sm" variant="ghost" disabled={step === "busy"} onClick={() => setStep("idle")}>
              Cancel
            </Button>
          </span>
        </span>
      ) : (
        <Button ref={triggerRef} size="sm" variant="ghost" onClick={() => setStep("confirm")}>
          Reset block…
        </Button>
      )}
      {error ? <span role="alert" className="text-xs text-text">Could not lift the block: {error}</span> : null}
    </span>
  );
}

function AccessTable({ rows, now, onReset }: { rows: readonly AccessRow[]; now: Date; onReset: (provider: string, endpoint: string) => Promise<string | null> }) {
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full min-w-[44rem] text-xs">
        <caption className="sr-only">Access state, freshness and quota of each endpoint of this provider</caption>
        <thead>
          <tr className="border-b border-border-soft text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <th scope="col" className="px-2 py-1 font-medium">Endpoint</th>
            <th scope="col" className="px-2 py-1 font-medium">Access</th>
            <th scope="col" className="px-2 py-1 font-medium">Last success</th>
            <th scope="col" className="px-2 py-1 font-medium">Quota</th>
            <th scope="col" className="px-2 py-1 font-medium">Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => (
            <tr key={a.id} className="border-b border-border-soft align-top last:border-0">
              <th scope="row" className="px-2 py-1.5 text-left font-mono font-normal break-all">{a.endpoint}</th>
              <td className="px-2 py-1.5">
                <DataStateBadge state={a.state} reason={a.detail} />
                <span className="mt-0.5 block text-text-muted">
                  ledger: {a.rawState}
                  {a.hardBlocked ? " · hard-blocked" : ""} · {a.accountTier} tier
                </span>
              </td>
              <td className="num px-2 py-1.5">
                {a.lastSuccessAt ? (
                  <>
                    <time dateTime={a.lastSuccessAt} title={formatDateTime(a.lastSuccessAt)}>{formatRelative(new Date(a.lastSuccessAt), now)}</time>
                    <span className="block text-text-muted">stale after {a.staleAfterHours} h</span>
                  </>
                ) : (
                  "Never succeeded"
                )}
              </td>
              <td className="num px-2 py-1.5">
                {a.quota === null ? (
                  "No published cap"
                ) : (
                  <>
                    {a.quotaUsed === null ? "?" : a.quotaUsed} of {a.quota} per {a.quotaPeriod ?? "window"}
                    {a.requestWeight !== null ? <span className="block text-text-muted">weight {a.requestWeight}</span> : null}
                  </>
                )}
              </td>
              <td className="px-2 py-1.5">
                <div className="flex flex-col items-start gap-1">
                  {a.retryAt ? (
                    <span className="num">Retry after {formatDateTime(a.retryAt)}</span>
                  ) : null}
                  {a.lastDenialAt ? (
                    <span className="break-words text-text-muted">
                      Last denial <time dateTime={a.lastDenialAt}>{formatDateTime(a.lastDenialAt)}</time>
                      {a.lastDenialState ? ` (${a.lastDenialState}` : ""}
                      {a.lastDenialStatusCode !== null ? `, HTTP ${a.lastDenialStatusCode}` : ""}
                      {a.lastDenialState ? ")" : ""}
                      {a.lastDenialDetail ? `: ${a.lastDenialDetail}` : ""}
                    </span>
                  ) : null}
                  {a.testedAt ? <span className="num text-text-muted">Tested {formatDate(a.testedAt.slice(0, 10))}</span> : null}
                  <ResetBlock row={a} onReset={onReset} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Provider × dataset × (held | watchlist | other) matrix from the coverage ledger. It is a
 * real table (providers as row groups): reading order is provider, dataset, held, watchlist,
 * other, and no state depends on colour. Each provider header carries the access summary of
 * its endpoints (with the endpoint table and the guarded reset action behind a disclosure)
 * and the drill-through to a failed run when any cell is failing.
 */
export function CoverageMatrix({
  groups,
  now,
  failedJobs,
  onResetBlock,
  scopeUnavailable = false,
}: {
  groups: readonly ProviderGroup[];
  now: Date;
  /** Newest failed jobs, or null while unknown. */
  failedJobs: readonly FailedJob[] | null;
  onResetBlock: (provider: string, endpoint: string) => Promise<string | null>;
  /** Held / watchlist lists failed to load: every security sits in "Other entities". */
  scopeUnavailable?: boolean;
}) {
  return (
    <div className="relative overflow-x-auto border border-border">
      <table className="w-full min-w-[60rem] text-sm">
        <caption className="sr-only">
          Source coverage: the datasets of each provider with access state, ledger-entry denominator and freshness for held securities, watchlist securities and other entities
        </caption>
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <th scope="col" className="px-3 py-2 text-left font-medium">Dataset</th>
            {ENTITY_SCOPES.map((s) => (
              <th key={s} scope="col" title={SCOPE_HINT[s]} className="px-3 py-2 text-left font-medium">
                {SCOPE_LABEL[s]}
              </th>
            ))}
          </tr>
        </thead>
        {groups.map((g) => {
          const counts = accessCounts(g.access);
          const failing = g.rows.some((r) => ENTITY_SCOPES.some((s) => r.cells[s].state === "unavailable" || r.cells[s].refreshFailing));
          const needsDrill = failing || g.access.some((a) => a.state === "unavailable");
          const drill = needsDrill && failedJobs ? drillThroughHref(g.provider, failedJobs) : null;
          return (
            <tbody key={g.provider} className="border-b border-border last:border-0">
              <tr className="bg-surface-muted/60">
                <th colSpan={4} scope="colgroup" className="px-3 py-1.5 text-left">
                  <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
                    <span className="font-heading text-[15px] font-semibold">{g.label}</span>
                    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs font-normal">
                      {g.access.length === 0 ? (
                        <span className="text-text-muted">No access ledger row</span>
                      ) : (
                        <span className="flex flex-wrap items-center gap-1">
                          <span className="text-text-muted">{g.access.length} endpoint{g.access.length === 1 ? "" : "s"}:</span>
                          {DATA_STATES.filter((s) => counts[s] > 0).map((s) => (
                            <span key={s} className="inline-flex items-center gap-1">
                              <DataStateBadge state={s} />
                              <span className="num text-text-muted">{counts[s]}</span>
                            </span>
                          ))}
                        </span>
                      )}
                      {drill ? (
                        <Link href={drill.href} className="inline-flex items-center gap-1 font-medium text-text underline hover:no-underline">
                          {drill.matched ? `Failed run #${drill.matched.id} (may be related)` : "Failed jobs in Activity"}
                          <ArrowRightIcon aria-hidden="true" className="size-3" />
                        </Link>
                      ) : null}
                    </span>
                  </div>
                  {g.access.length > 0 ? (
                    <details className="mt-1 font-normal">
                      <summary className="cursor-pointer text-xs font-medium text-accent-ink select-none hover:underline">
                        Endpoint access, freshness and quota
                      </summary>
                      <div className="mt-1 border border-border-soft bg-surface-raised">
                        <AccessTable rows={g.access} now={now} onReset={onResetBlock} />
                      </div>
                    </details>
                  ) : null}
                </th>
              </tr>
              {g.rows.length === 0 ? (
                <tr className="border-t border-border-soft">
                  <td colSpan={4} className="px-3 py-2 text-xs text-text-muted">
                    {g.hiddenRows && g.hiddenRows > 0
                      ? `No datasets of this provider have entries in the selected universe; ${g.hiddenRows} dataset${g.hiddenRows === 1 ? " has" : "s have"} entries elsewhere (change the universe to see them).`
                      : "No coverage-ledger entries for this provider yet: its datasets are unknown, not covered and not zero."}
                  </td>
                </tr>
              ) : null}
              {g.rows.map((r) => (
                <tr key={r.id} className="border-t border-border-soft align-top">
                  <th scope="row" className="px-3 py-2 text-left font-normal">
                    <span className="font-medium break-words">{r.dataset}</span>
                  </th>
                  {ENTITY_SCOPES.map((s) => (
                    <td key={s} className={cn("px-3 py-2", s === "other" && scopeUnavailable && "bg-surface-muted/40")}>
                      <Cell cell={r.cells[s]} now={now} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          );
        })}
      </table>
    </div>
  );
}
