import { HBar, Sparkline } from "@/components/charts";
import { DataStateBadge } from "@/components/data-state";
import { budgetTone, describeBudget, routineHeadroom, usedFraction } from "@/lib/source-coverage/logic";
import { PROVIDER_LABELS, type BudgetUsage } from "@/lib/source-coverage/types";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const TONE_LABEL = {
  ok: "Within budget",
  reserve: "Reserve only",
  exhausted: "Exhausted",
  uncapped: "No published cap",
} as const;

/**
 * Compact request-budget burn: one row per provider with the spend against its free
 * allowance (the shaded end of the bar is the retry/urgent reserve), the previous 14
 * windows as a sparkline, demand deferred to the next reset and any overage. A provider
 * without a published cap draws no bar: an empty track would read as "nothing used".
 */
export function BudgetBurn({ budgets }: { budgets: readonly BudgetUsage[] }) {
  return (
    <div className="flex flex-col gap-2">
      <ul className="m-0 grid list-none grid-cols-1 gap-x-6 gap-y-3 p-0 lg:grid-cols-2">
        {budgets.map((b) => {
          const tone = budgetTone(b);
          const frac = usedFraction(b);
          const headroom = routineHeadroom(b);
          return (
            <li key={b.provider} data-tone={tone} className="flex flex-col gap-1 border-b border-border-soft pb-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="font-medium">{PROVIDER_LABELS[b.provider]}</span>
                <span className="num text-xs text-text-muted">{describeBudget(b)}</span>
              </div>
              {b.limit !== null ? (
                <HBar
                  label={`${PROVIDER_LABELS[b.provider]} spend against its free allowance`}
                  value={Math.min(b.used, b.limit)}
                  max={b.limit}
                  band={b.reserve > 0 ? [b.limit - b.reserve, b.limit] : null}
                  tone={tone === "exhausted" ? "loss" : tone === "reserve" ? "warning" : "accent"}
                  formatValue={(v) => `${v} ${b.unit}`}
                />
              ) : (
                <div className="h-2 border border-dashed border-border" aria-hidden="true" />
              )}
              <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs text-text-muted">
                <span className="inline-flex items-center gap-1.5">
                  <span
                    className={cn(
                      "font-medium",
                      tone === "exhausted" || tone === "reserve" ? "text-text" : "text-text-muted",
                    )}
                  >
                    {TONE_LABEL[tone]}
                    {frac !== null ? ` · ${Math.round(frac * 100)}%` : ""}
                  </span>
                  {headroom !== null ? <span className="num">· {headroom} {b.unit} routine headroom</span> : null}
                </span>
                <span className="inline-flex items-center gap-2">
                  {b.deferred > 0 ? <span className="num text-text">{b.deferred} deferred to next reset</span> : null}
                  {b.resetsAt ? <span className="num">resets {formatDateTime(b.resetsAt)}</span> : null}
                  <Sparkline
                    values={b.history}
                    width={72}
                    height={18}
                    label={`${PROVIDER_LABELS[b.provider]}, ${b.history.length} previous ${b.period}s`}
                    formatValue={(v) => `${v} ${b.unit}`}
                  />
                </span>
              </div>
              {b.accessState !== "verified" ? (
                <div className="flex items-center gap-1.5 text-xs">
                  <DataStateBadge state={b.accessState} />
                  <span className="text-text-muted">Quota is not assumed; no scheduled job spends it.</span>
                </div>
              ) : null}
              {b.overageEvents > 0 ? (
                <p role="alert" className="text-xs font-medium text-text">
                  {b.overageEvents} overage event{b.overageEvents === 1 ? "" : "s"} recorded: release blocker.
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
