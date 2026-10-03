"use client";

import { LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { DataStateBadge, Segmented, StateLegend } from "@/components/data-state";
import { BudgetBurn } from "@/components/sources/budget-burn";
import { CoverageMatrix } from "@/components/sources/coverage-matrix";
import { Button } from "@/components/ui/button";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { DATA_STATES, type DataState } from "@/lib/data-state/states";
import type { ResourceState } from "@/lib/hooks/use-api-resource";
import { useNow } from "@/lib/hooks/use-now";
import {
  resetProviderBlock,
  useDataCoverage,
  useEntityScopes,
  useFailedJobs,
  useSourceAccess,
  useSourceStatus,
  useSourceUsage,
  useZeroCostGate,
} from "@/lib/source-coverage/hooks";
import {
  accessCounts,
  buildBudgets,
  buildMatrix,
  filterGroups,
  gateVerdict,
  overAllowance,
  stateCounts,
  type ScopeFilter,
  type StateFilter,
} from "@/lib/source-coverage/logic";
import { providerLabel } from "@/lib/source-coverage/providers";
import type { ZeroCostGate } from "@/lib/source-coverage/types";

const SCOPES = [
  { id: "all", label: "All entities" },
  { id: "held", label: "Held" },
  { id: "watchlist", label: "Watchlist" },
  { id: "other", label: "Other" },
] as const satisfies readonly { id: ScopeFilter; label: string }[];

/** The data to draw: the current result, or the last good one kept beside an error. */
function dataOf<T>(state: ResourceState<T>): T | null {
  return state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
}

function LoadError({ what, state }: { what: string; state: ResourceState<unknown> }) {
  if (state.kind !== "error") return null;
  return (
    <AlertMessage title={`Could not load ${what}`}>
      {state.title}
      {state.detail ? `: ${state.detail}` : ""}
      {state.previous !== null ? " The last good data stays on screen." : ""}
    </AlertMessage>
  );
}

export function GateSummary({ gate }: { gate: ZeroCostGate }) {
  const violations = gate.findings.filter((f) => f.severity === "violation");
  const warnings = gate.findings.filter((f) => f.severity === "warning");
  const verdict = gateVerdict(gate);
  return (
    <div className="flex flex-col gap-1 border border-border-soft px-3 py-2 text-sm">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-medium text-text">
          Zero-cost gate:{" "}
          {verdict === "passed"
            ? "passed"
            : verdict === "not_verified"
              ? "not verified (no manifest endpoints were checked)"
              : `failed (${gate.violationCount} violation${gate.violationCount === 1 ? "" : "s"})`}
        </span>
        <span className="num text-xs text-text-muted">
          {gate.checked} manifest endpoints checked · {gate.freeAvailable} free and available · {gate.disabledOk} disabled for a permitted reason ·{" "}
          {gate.gatedUnverified} gated until verified · {gate.warningCount} warning{gate.warningCount === 1 ? "" : "s"}
        </span>
      </p>
      <p className="text-xs text-text-muted">
        The audit reads the endpoint manifest, access ledger and collector budgets; it reports credits, not dollars, and the API exposes no dollar spend figure.
      </p>
      {violations.length > 0 ? (
        <ul role="alert" className="m-0 list-disc pl-5 text-xs text-text">
          {violations.map((f) => (
            <li key={`${f.provider}/${f.endpoint}/${f.code}/${f.message}`} className="break-words">
              <span className="font-mono">{f.provider}/{f.endpoint}</span> · {f.code}: {f.message}
            </li>
          ))}
        </ul>
      ) : null}
      {warnings.length > 0 ? (
        <details>
          <summary className="cursor-pointer text-xs font-medium text-accent-ink select-none hover:underline">
            {warnings.length} warning{warnings.length === 1 ? "" : "s"} ({Object.entries(gate.warningsByCode).map(([k, n]) => `${k} ${n}`).join(", ")})
          </summary>
          <ul className="m-0 mt-1 list-disc pl-5 text-xs text-text-muted">
            {warnings.map((f) => (
              <li key={`${f.provider}/${f.endpoint}/${f.code}/${f.message}`} className="break-words">
                <span className="font-mono">{f.provider}/{f.endpoint}</span> · {f.code}: {f.message}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

/**
 * Source coverage: which provider datasets are usable for which securities, their access
 * state and freshness, and how much of each free allowance is spent. Everything comes from
 * the owner-only read APIs (`/data/coverage`, `/sources/access|usage|status`,
 * `/verification/zero-cost-gate`); a failed reload keeps the last data on screen beside the
 * error, and a section that cannot load says so instead of drawing zeros.
 */
export function SourceCoveragePanel() {
  const coverage = useDataCoverage();
  const access = useSourceAccess();
  const usage = useSourceUsage();
  const status = useSourceStatus();
  const gate = useZeroCostGate();
  const scopes = useEntityScopes();
  const failed = useFailedJobs();
  const now = useNow(60_000);

  const [scope, setScope] = useState<ScopeFilter>("all");
  const [state, setState] = useState<StateFilter>("all");
  const [provider, setProvider] = useState<string>("all");
  const [text, setText] = useState("");

  const entries = dataOf(coverage.state);
  const accessRows = dataOf(access.state);
  const scopeSets = dataOf(scopes.state);
  const gateData = dataOf(gate.state);

  const groups = useMemo(() => (entries ? buildMatrix(entries, scopeSets, accessRows ?? []) : null), [entries, scopeSets, accessRows]);
  const counts = useMemo(() => (groups ? stateCounts(groups, scope) : null), [groups, scope]);
  const visible = useMemo(() => {
    if (!groups) return [];
    const q = text.trim().toLowerCase();
    return filterGroups(groups, { scope, state, provider })
      .map((g) => ({ ...g, rows: q ? g.rows.filter((r) => r.dataset.toLowerCase().includes(q)) : g.rows }))
      .filter((g) => !q || g.rows.length > 0);
  }, [groups, scope, state, provider, text]);
  const providers = useMemo(() => (groups ? groups.map((g) => g.provider) : []), [groups]);
  const budgets = useMemo(() => (accessRows ? buildBudgets(accessRows, dataOf(usage.state) ?? [], dataOf(status.state) ?? []) : null), [accessRows, usage.state, status.state]);
  const over = budgets ? overAllowance(budgets) : [];
  const failedJobs = dataOf(failed.state);

  const reloadAll = useCallback(() => {
    coverage.reload();
    access.reload();
    usage.reload();
    status.reload();
    gate.reload();
    scopes.reload();
    failed.reload();
  }, [coverage, access, usage, status, gate, scopes, failed]);

  const resetBlock = useCallback(
    async (p: string, e: string) => {
      const failure = await resetProviderBlock(p, e);
      if (!failure) {
        access.reload();
        coverage.reload();
      }
      return failure;
    },
    [access, coverage],
  );

  const loading = coverage.state.kind === "loading" || access.state.kind === "loading";

  return (
    <Blueprint as="section" id="source-coverage" aria-labelledby="source-coverage-heading" className="scroll-mt-24 space-y-4 px-5 py-5">
      <PanelHeading
        kicker="Free data sources"
        title={<span id="source-coverage-heading">Source coverage and request budget</span>}
        action={
          <Button variant="ghost" size="sm" onClick={reloadAll}>
            <RefreshCwIcon aria-hidden="true" />
            Reload
          </Button>
        }
      />
      <p className="max-w-prose text-sm text-text-muted">
        Every provider dataset in the coverage ledger, split into held securities, watchlist securities and other entities, with its access state, entry
        denominator and how fresh its newest verified value is. A failing refresh keeps the last verified value; a paid-only or unverified endpoint collects
        nothing. Counts are of ledger entries, not of the whole universe: an entity with no entry is unknown.
      </p>

      {loading ? (
        <p role="status" className="flex items-center gap-2 text-sm text-text-muted">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading source coverage…
        </p>
      ) : null}
      <LoadError what="the coverage ledger" state={coverage.state} />
      <LoadError what="provider access" state={access.state} />

      {gateData ? <GateSummary gate={gateData} /> : null}
      <LoadError what="the zero-cost gate" state={gate.state} />
      {gate.state.kind === "loading" ? <p className="text-xs text-text-muted">Checking the zero-cost gate…</p> : null}

      {access.state.kind !== "loading" && accessRows ? (
        <section aria-labelledby="source-budget-heading" className="space-y-2">
          <h3 id="source-budget-heading" className="font-heading text-lg font-semibold">
            Request budget
          </h3>
          <p className="num text-xs text-text-muted">
            Allowances are each endpoint&apos;s declared free quota; use is the current window&apos;s ledger credits.
            {" "}
            {over.length > 0 ? `${over.length} endpoint${over.length === 1 ? "" : "s"} over the declared allowance.` : "No endpoint is over its declared allowance."}
          </p>
          <LoadError what="usage windows" state={usage.state} />
          <LoadError what="provider spend" state={status.state} />
          {budgets ? <BudgetBurn budgets={budgets} /> : null}
        </section>
      ) : null}

      {groups && counts && accessRows ? (
        <section aria-labelledby="source-matrix-heading" className="space-y-3">
          <h3 id="source-matrix-heading" className="font-heading text-lg font-semibold">
            Dataset matrix
          </h3>
          <p role="status" className="num text-xs text-text-muted">
            {entries?.length ?? 0} ledger entries across {groups.length} provider{groups.length === 1 ? "" : "s"}
            {scopeSets ? ` · ${scopeSets.held.size} held and ${scopeSets.watchlist.size} watchlist ticker keys matched against them` : ""}
            {accessRows ? ` · access ${Object.entries(accessCounts(accessRows)).filter(([, n]) => n > 0).map(([k, n]) => `${n} ${k.replace("_", " ")}`).join(", ")}` : ""}
          </p>
          {scopes.state.kind === "error" ? (
            <AlertMessage title="Held and watchlist membership unavailable">
              {scopes.state.title}. Securities are listed under &quot;Other entities&quot; until the holdings and watchlist load; nothing is guessed.
            </AlertMessage>
          ) : null}
          <div className="flex flex-wrap items-center gap-3">
            <Segmented label="Universe" options={SCOPES} value={scope} onChange={setScope} />
            <label className="flex items-center gap-1.5 text-xs text-text-muted">
              Provider
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="border border-border bg-surface-raised px-2 py-1 text-xs text-text"
              >
                <option value="all">All providers</option>
                {providers.map((p) => (
                  <option key={p} value={p}>
                    {providerLabel(p)}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex min-w-0 items-center gap-1.5 text-xs text-text-muted">
              Dataset
              <input
                type="search"
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Filter by name"
                className="min-w-0 border border-border bg-surface-raised px-2 py-1 text-xs text-text"
              />
            </label>
          </div>
          <div role="group" aria-label="Filter by state" className="flex flex-wrap gap-1.5">
            <button
              type="button"
              aria-pressed={state === "all"}
              onClick={() => setState("all")}
              className="border border-border px-2 py-0.5 text-xs font-medium aria-pressed:bg-accent aria-pressed:text-accent-foreground"
            >
              All states
            </button>
            {DATA_STATES.filter((s) => counts[s] > 0).map((s: DataState) => (
              <button
                key={s}
                type="button"
                aria-pressed={state === s}
                onClick={() => setState(state === s ? "all" : s)}
                className="inline-flex items-center gap-1 outline-hidden focus-visible:outline-2 focus-visible:outline-focus aria-pressed:outline-2 aria-pressed:outline-text"
              >
                <DataStateBadge state={s} />
                <span className="num text-xs text-text-muted">{counts[s]}</span>
              </button>
            ))}
          </div>
          {visible.length === 0 ? (
            <EmptyState title={groups.length === 0 ? "The coverage ledger is empty" : "No datasets match"}>
              {groups.length === 0
                ? "No provider has recorded a coverage entry yet. That means unknown, not covered and not zero."
                : "Clear a filter to see every provider dataset."}
            </EmptyState>
          ) : (
            <CoverageMatrix groups={visible} now={now} failedJobs={failedJobs} onResetBlock={resetBlock} scopeUnavailable={scopes.state.kind === "error" && !scopeSets} />
          )}
        </section>
      ) : null}

      <details className="border border-border-soft">
        <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-accent-ink select-none hover:underline">What each state means</summary>
        <div className="border-t border-border-soft p-3">
          <StateLegend counts={counts ?? undefined} />
        </div>
      </details>
    </Blueprint>
  );
}
