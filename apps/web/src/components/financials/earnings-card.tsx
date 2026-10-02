import { ProvenanceFooter } from "@/components/data-state";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { linearScale } from "@/lib/charts/scale";
import { surprise } from "@/lib/financials/logic";
import type { EarningsEvent } from "@/lib/financials/types";
import { formatDate } from "@/lib/format";

const POSITION_TEXT = {
  above_range: "Above the analyst range",
  within_range: "Within the analyst range",
  below_range: "Below the analyst range",
  no_range: "No analyst range available",
} as const;

/**
 * Actual versus the consensus that was available, for one reported figure. Without a published
 * consensus it says so and shows only the actual: there is no model-made estimate to compare to.
 */
export function EarningsCard({ event }: { event: EarningsEvent }) {
  const s = surprise(event);
  const hasRange = event.consensusLow !== null && event.consensusHigh !== null && event.consensusMean !== null;
  const lo = hasRange ? Math.min(event.consensusLow!, event.actual) : 0;
  const hi = hasRange ? Math.max(event.consensusHigh!, event.actual) : 1;
  const pad = (hi - lo) * 0.15 || 1;
  const x = linearScale([lo - pad, hi + pad], [0, 100]);
  return (
    <article className="flex flex-col gap-3 border border-border-soft p-4" aria-label={`${event.metric} ${event.period}: actual versus consensus`}>
      <header>
        <p className="kicker">{event.period} · reported {formatDate(event.reportedAt)}</p>
        <h3 className="font-heading text-[19px] leading-tight font-semibold">{event.metric}: actual vs available estimate</h3>
      </header>
      <div className="flex items-baseline gap-4">
        <div>
          <p className="text-xs text-text-muted">Actual</p>
          <p className="num font-heading text-[30px] leading-none font-semibold">{formatNumber(event.actual, event.actual >= 100 ? 0 : 2)}</p>
        </div>
        <div>
          <p className="text-xs text-text-muted">Consensus mean</p>
          <p className="num font-heading text-[30px] leading-none font-semibold text-text-muted">
            {event.consensusMean === null ? "n/a" : formatNumber(event.consensusMean, 2)}
          </p>
        </div>
        <div>
          <p className="text-xs text-text-muted">Surprise</p>
          <p className="num font-heading text-[30px] leading-none font-semibold">{s.pct === null ? "n/a" : formatPct(s.pct, 1, true)}</p>
        </div>
      </div>
      {hasRange ? (
        <div className="flex flex-col gap-1">
          <div
            role="img"
            aria-label={`${event.metric}: actual ${formatNumber(event.actual, 2)} is ${POSITION_TEXT[s.position].toLowerCase()} of ${formatNumber(event.consensusLow!, 2)} to ${formatNumber(event.consensusHigh!, 2)}`}
            className="relative h-6"
          >
            <span className="absolute top-2.5 h-1 bg-accent-soft outline outline-1 outline-accent" style={{ left: `${x(event.consensusLow!)}%`, width: `${x(event.consensusHigh!) - x(event.consensusLow!)}%` }} />
            <span className="absolute top-1 h-4 w-0.5 bg-accent-ramp-1" style={{ left: `${x(event.consensusMean!)}%` }} />
            <span className="absolute top-0 flex -translate-x-1/2 flex-col items-center" style={{ left: `${x(event.actual)}%` }}>
              <i aria-hidden="true" className="block size-3 rotate-45 bg-text" />
            </span>
          </div>
          <p className="text-xs text-text-muted">
            ◆ actual · bar = mean · band = analyst range {formatNumber(event.consensusLow!, 2)} to {formatNumber(event.consensusHigh!, 2)}
            {event.analystCount !== null ? `, ${event.analystCount} analysts` : ""}. <strong className="font-medium text-text">{POSITION_TEXT[s.position]}.</strong>
          </p>
        </div>
      ) : (
        <p role="status" className="border border-dashed border-border px-3 py-2 text-sm text-text-muted">
          <strong className="font-medium text-text">No published consensus for this figure.</strong> No estimate is shown or created; the actual stands alone.
        </p>
      )}
      <ProvenanceFooter
        provenance={{
          source: event.provider ?? "No consensus source",
          asOf: event.reportedAt,
          method: "Surprise = actual ÷ consensus mean − 1, on the provider's stated basis",
          state: hasRange ? "verified" : "not_covered",
          reason: hasRange ? null : "No free source publishes a consensus for this figure.",
        }}
      />
    </article>
  );
}
