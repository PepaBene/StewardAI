import { CircleCheckIcon, CircleHelpIcon, CircleMinusIcon, CircleXIcon, LoaderCircleIcon, type LucideIcon } from "lucide-react";

import { DataStateBadge } from "@/components/data-state";
import { formatReading, REASON_HELP, REASON_LABEL, UNKNOWN_REASONS } from "@/lib/screens/logic";
import { metricDef } from "@/lib/screens/metrics";
import type { Criterion, Entity, Outcome, OutcomeKind, ScreenSummary, UnknownReason } from "@/lib/screens/types";
import { cn } from "@/lib/utils";
import type { DataState } from "@/lib/data-state/states";

const OUTCOME: Record<OutcomeKind, { label: string; Icon: LucideIcon; cls: string }> = {
  matched: { label: "Matched", Icon: CircleCheckIcon, cls: "border-verified/40 bg-verified-subtle" },
  failed: { label: "Failed a criterion", Icon: CircleXIcon, cls: "border-border bg-surface-muted" },
  excluded: { label: "Excluded (unknown data)", Icon: CircleMinusIcon, cls: "border-warning-signal/40 bg-warning-signal-subtle" },
  unknown: { label: "Unknown", Icon: CircleHelpIcon, cls: "border-info-signal/40 bg-info-signal-subtle" },
  pending: { label: "Loading", Icon: LoaderCircleIcon, cls: "border-border" },
};

export function OutcomeBadge({ kind }: { kind: OutcomeKind }) {
  const o = OUTCOME[kind];
  return (
    <span className={cn("inline-flex items-center gap-1 border px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", o.cls)}>
      <o.Icon aria-hidden="true" className={cn("size-3.5", kind === "pending" && "animate-spin")} />
      {o.label}
    </span>
  );
}

const REASON_STATE: Partial<Record<UnknownReason, DataState>> = {
  stale: "stale",
  not_covered: "not_covered",
  paid_only: "paid_only",
  unavailable: "unavailable",
};

/** An unknown figure's reason: the shared state badge where one exists, else a "Missing metric" chip. */
export function ReasonBadge({ reason, detail }: { reason: UnknownReason; detail?: string }) {
  const state = REASON_STATE[reason];
  if (state) return <DataStateBadge state={state} reason={detail} />;
  return (
    <span title={detail ? `${REASON_HELP[reason]} ${detail}` : REASON_HELP[reason]} className="inline-flex items-center gap-1 border border-border bg-neutral-signal-subtle px-1.5 py-0.5 text-xs font-medium whitespace-nowrap">
      <CircleHelpIcon aria-hidden="true" className="size-3.5" />
      {REASON_LABEL[reason]}
    </span>
  );
}

/** Universe, matched, failed, excluded / unknown counts and the reasons behind the unknowns. */
export function SummaryTiles({ s, treatment, hasCriteria }: { s: ScreenSummary; treatment: "exclude" | "unknown"; hasCriteria: boolean }) {
  const tile = (label: string, value: number | string, hint: string) => (
    <div className="flex min-w-0 flex-col gap-0.5 border border-border-soft p-3">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="num m-0 text-2xl font-semibold">{value}</dd>
      <dd className="m-0 text-[11px] text-text-faint">{hint}</dd>
    </div>
  );
  const unknownCount = s.excluded + s.unknown;
  return (
    <div className="flex flex-col gap-3">
      <dl className="m-0 grid grid-cols-2 gap-3 md:grid-cols-4">
        {tile("Universe size", s.universe, "held + watchlist tickers in scope")}
        {tile("Matched", hasCriteria ? s.matched : "n/a", hasCriteria ? "every criterion passed on known data" : "add a criterion")}
        {tile("Failed", hasCriteria ? s.failed : "n/a", "known value failed a criterion")}
        {tile(treatment === "exclude" ? "Excluded" : "Unknown", hasCriteria ? unknownCount : "n/a", treatment === "exclude" ? "dropped: a metric is unknown" : "listed, not matched: a metric is unknown")}
      </dl>
      {hasCriteria && unknownCount > 0 ? (
        <div className="text-xs text-text-muted">
          <p className="mb-1 font-medium text-text">Why entities have unknown data (an entity can have several reasons)</p>
          <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0">
            {UNKNOWN_REASONS.filter((r) => s.byReason[r] > 0).map((r) => (
              <li key={r} className="flex items-center gap-1.5">
                <ReasonBadge reason={r} />
                <span className="num">{s.byReason[r]}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {s.pending > 0 ? <p role="status" className="text-xs text-text-muted">{s.pending} entities are still loading their valuation figures and are not counted as matched or excluded yet.</p> : null}
    </div>
  );
}

/** The per-entity result table: outcome, then one cell per criterion with the value or its unknown reason. */
export function ResultsTable({ outcomes, criteria }: { outcomes: readonly Outcome[]; criteria: readonly Criterion[] }) {
  if (outcomes.length === 0) return <p className="text-sm text-text-muted">No entity in this view.</p>;
  const cols = criteria.map((c) => ({ def: metricDef(c.metric)!, op: c.op, value: c.value }));
  return (
    <div className="relative max-h-[32rem] overflow-auto border border-border-soft">
      <table className="w-full min-w-[40rem] text-xs">
        <caption className="sr-only">Screen result per entity</caption>
        <thead className="sticky top-0 bg-surface-muted">
          <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <th scope="col" className="px-2 py-1.5 font-medium">
              Entity
            </th>
            <th scope="col" className="px-2 py-1.5 font-medium">
              Result
            </th>
            {cols.map((c, i) => (
              <th key={i} scope="col" className="px-2 py-1.5 font-medium" title={`Source: ${c.def.endpoint}`}>
                {c.def.label} {c.op === "gte" ? "≥" : "≤"} {c.value}
                {c.def.unit === "pct" ? "%" : "x"}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {outcomes.map((o) => (
            <tr key={o.entity.ticker} className="border-b border-border-soft align-top last:border-0">
              <th scope="row" className="px-2 py-1.5 text-left font-normal">
                <span className="num block font-medium break-all">{o.entity.ticker}</span>
                <span className="block text-text-muted">{[o.entity.name, o.entity.held ? "held" : null, o.entity.watchlist ? "watchlist" : null].filter(Boolean).join(" · ")}</span>
              </th>
              <td className="px-2 py-1.5">
                <OutcomeBadge kind={o.kind} />
              </td>
              {o.results.map((r, i) => {
                const def = metricDef(r.criterion.metric)!;
                return (
                  <td key={i} className="px-2 py-1.5">
                    {r.reading.status === "value" ? (
                      <span className="num inline-flex items-center gap-1">
                        {r.verdict === "pass" ? <CircleCheckIcon aria-hidden="true" className="size-3.5" /> : <CircleXIcon aria-hidden="true" className="size-3.5" />}
                        {formatReading(r.reading, def)}
                        <span className="sr-only">{r.verdict === "pass" ? " passes" : " fails"}</span>
                      </span>
                    ) : (
                      <div className="flex flex-col items-start gap-1">
                        <ReasonBadge reason={r.reading.reason} detail={r.reading.detail} />
                        {r.reading.staleValue !== undefined ? <span className="num text-text-muted">{formatReading(r.reading, def)}</span> : null}
                        <span className="max-w-[16rem] text-text-muted">{r.reading.detail}</span>
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The universe before any criterion is complete: who is in scope, with no result claimed. */
export function UniverseTable({ entities }: { entities: readonly Entity[] }) {
  if (entities.length === 0) return <p className="text-sm text-text-muted">The universe is empty: no held position and no watchlist item in this scope.</p>;
  return (
    <div className="relative max-h-[24rem] overflow-auto border border-border-soft">
      <table className="w-full min-w-[20rem] text-xs">
        <caption className="sr-only">Entities in the screen universe</caption>
        <thead className="sticky top-0 bg-surface-muted">
          <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
            <th scope="col" className="px-2 py-1.5 font-medium">Entity</th>
            <th scope="col" className="px-2 py-1.5 font-medium">In universe as</th>
          </tr>
        </thead>
        <tbody>
          {entities.map((e) => (
            <tr key={e.ticker} className="border-b border-border-soft last:border-0">
              <th scope="row" className="num px-2 py-1.5 text-left font-medium break-all">{e.ticker}{e.name ? <span className="font-normal text-text-muted"> · {e.name}</span> : null}</th>
              <td className="px-2 py-1.5">{[e.held ? "held" : null, e.watchlist ? "watchlist" : null].filter(Boolean).join(" and ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
