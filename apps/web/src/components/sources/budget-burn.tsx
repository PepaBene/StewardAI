import { HBar, Sparkline } from "@/components/charts";
import { DataStateBadge, TableView } from "@/components/data-state";
import { formatDateTime } from "@/lib/format";
import { TONE_LABEL, describeBudget, usedFraction } from "@/lib/source-coverage/logic";
import type { BudgetItem } from "@/lib/source-coverage/types";
import { cn } from "@/lib/utils";

const BAR_TONE = { ok: "accent", low: "warning", exhausted: "loss", uncapped: "accent", untracked: "accent" } as const;

function Row({ b }: { b: BudgetItem }) {
  const frac = usedFraction(b);
  const label = `${b.providerLabel} ${b.endpoint} credits used against its free allowance`;
  return (
    <li data-tone={b.tone} className="flex min-w-0 flex-col gap-1 border-b border-border-soft pb-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
        <span className="min-w-0 font-medium break-words">
          {b.providerLabel} <span className="font-mono text-xs font-normal text-text-muted">{b.endpoint}</span>
        </span>
        <span className="num text-xs text-text-muted">{describeBudget(b)}</span>
      </div>
      {b.limit !== null && b.used !== null ? (
        <HBar
          label={label}
          value={Math.min(b.used, b.limit)}
          max={b.limit}
          tone={BAR_TONE[b.tone]}
          formatValue={(v) => `${v} of ${b.limit} credits`}
        />
      ) : (
        <div className="h-2 border border-dashed border-border" aria-hidden="true" />
      )}
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-text-muted">
        <span className={cn("font-medium", b.tone === "exhausted" || b.tone === "low" ? "text-text" : "text-text-muted")}>
          {TONE_LABEL[b.tone]}
          {frac !== null ? ` · ${Math.round(frac * 100)}% used` : ""}
          {b.remaining !== null ? <span className="num font-normal"> · {b.remaining} left</span> : null}
        </span>
        <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1">
          {b.resetsAt ? (
            <span className="num">
              resets {formatDateTime(b.resetsAt)}
              {b.resetTimezone ? ` (window zone ${b.resetTimezone})` : ""}
            </span>
          ) : b.limit !== null ? (
            <span>reset time not reported</span>
          ) : null}
          {b.history.length >= 2 ? (
            <Sparkline
              values={b.history}
              width={72}
              height={18}
              label={`${b.providerLabel} ${b.endpoint}, ${b.history.length} recorded ${b.period ?? ""} windows`}
              formatValue={(v) => `${v} credits`}
            />
          ) : null}
        </span>
      </div>
      {b.state !== "verified" ? (
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <DataStateBadge state={b.state} />
          <span className="text-text-muted">Access state of this endpoint; the allowance is not assumed to be spendable.</span>
        </div>
      ) : null}
      {b.note && b.tone !== "ok" ? <p className="text-xs text-text-muted">{b.note}</p> : null}
      {b.limit !== null && b.used !== null && b.used > b.limit ? (
        <p role="alert" className="text-xs font-medium text-text">
          Recorded use ({b.used}) exceeds the declared allowance ({b.limit}).
        </p>
      ) : null}
    </li>
  );
}

/**
 * Request-budget burn from the real ledgers: the cap is the endpoint's declared free quota
 * (`/sources/access`), the spend its current-window credits, the history the recorded usage
 * windows (`/sources/usage`). The API publishes no reserve or deferred demand, so none is
 * drawn; an endpoint with no cap shows "No published cap" and no bar (an empty track would
 * read as "nothing used"). Capped endpoints are charted; the full list is in the table.
 */
export function BudgetBurn({ budgets }: { budgets: readonly BudgetItem[] }) {
  const charted = budgets.filter((b) => b.limit !== null);
  const uncapped = budgets.filter((b) => b.limit === null);
  const ordered = [...charted].sort((a, b) => (usedFraction(b) ?? -1) - (usedFraction(a) ?? -1));
  return (
    <div className="flex flex-col gap-3">
      {charted.length === 0 ? (
        <p className="text-sm text-text-muted">No endpoint publishes a free-tier cap in the access ledger, so there is no allowance to chart.</p>
      ) : (
        <ul className="m-0 grid list-none grid-cols-1 gap-x-6 gap-y-3 p-0 lg:grid-cols-2">
          {ordered.map((b) => (
            <Row key={b.id} b={b} />
          ))}
        </ul>
      )}
      {uncapped.length > 0 ? (
        <p className="text-xs text-text-muted">
          {uncapped.length} endpoint{uncapped.length === 1 ? "" : "s"} with no published cap (listed in the table below; their use is not measured against an allowance).
        </p>
      ) : null}
      {budgets.length > 0 ? (
        <TableView
          summary="Table view: every endpoint's allowance and use"
          table={{
            caption: "Request budget per provider endpoint: cap, credits used in the current window, credits spent today and this month",
            columns: ["Provider · endpoint", "Cap", "Used (window)", "Left", "Today", "This month", "Window resets"],
            rows: budgets.map((b) => [
              `${b.providerLabel} · ${b.endpoint}`,
              b.limit === null ? "No published cap" : `${b.limit} per ${b.period ?? "window"}`,
              b.used === null ? "not tracked" : String(b.used),
              b.remaining === null ? "n/a" : String(b.remaining),
              b.spentDay === null ? "n/a" : String(b.spentDay),
              b.spentMonth === null ? "n/a" : String(b.spentMonth),
              b.resetsAt ? formatDateTime(b.resetsAt) : "not reported",
            ]),
          }}
        />
      ) : null}
    </div>
  );
}
