import { ProvenanceFooter } from "@/components/data-state";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { linearScale } from "@/lib/charts/scale";
import { avcState, surprise } from "@/lib/financials/logic";
import type { ActualVsConsensus } from "@/lib/financials/types";
import { formatDate } from "@/lib/format";

const POSITION_TEXT = {
  above_range: "Above the analyst range",
  within_range: "Within the analyst range",
  below_range: "Below the analyst range",
  no_range: "No analyst range available",
} as const;

function fmt(v: number): string {
  return formatNumber(v, Math.abs(v) >= 1000 ? 0 : 2);
}

/**
 * The reported figure of a fiscal period against the last consensus known BEFORE the report
 * (the API's rule; a later vintage is not "the expectation"). Without consensus it says so
 * and shows only the actual; on a currency mismatch it shows both and computes no surprise.
 */
export function EarningsCard({ avc }: { avc: ActualVsConsensus }) {
  const status = avcState(avc);
  const s = surprise(avc);
  const c = avc.consensus;
  const actual = avc.actual;
  const hasRange = !!c && c.low !== null && c.high !== null && c.value !== null && !!actual;
  const surpriseOk = avc.state === "available";
  const title = `${avc.metric ?? "Metric"} ${avc.fiscalPeriod ?? ""}: actual vs the consensus known before the report`;

  let lo = 0;
  let hi = 1;
  if (hasRange && actual && c) {
    lo = Math.min(c.low as number, actual.value);
    hi = Math.max(c.high as number, actual.value);
  }
  const pad = (hi - lo) * 0.15 || 1;
  const x = linearScale([lo - pad, hi + pad], [0, 100]);

  return (
    <article className="flex min-w-0 flex-col gap-3 border border-border-soft p-4" aria-label={title}>
      <header>
        <p className="kicker">
          {avc.fiscalPeriod ?? "—"}
          {actual?.knownAt ? ` · filed ${formatDate(actual.knownAt.slice(0, 10))}` : ""}
        </p>
        <h3 className="font-heading text-[19px] leading-tight font-semibold">{title}</h3>
      </header>
      {actual ? (
        <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
          <div>
            <p className="text-xs text-text-muted">Actual{actual.currency ? ` (${actual.currency})` : ""}</p>
            <p className="num font-heading text-[30px] leading-none font-semibold">{fmt(actual.value)}</p>
          </div>
          <div>
            <p className="text-xs text-text-muted">Consensus mean{c?.currency ? ` (${c.currency})` : ""}</p>
            <p className="num font-heading text-[30px] leading-none font-semibold text-text-muted">{c && c.value !== null ? fmt(c.value) : "n/a"}</p>
          </div>
          <div>
            <p className="text-xs text-text-muted">Surprise</p>
            <p className="num font-heading text-[30px] leading-none font-semibold">{surpriseOk && s.pct !== null ? formatPct(s.pct, 1, true) : "n/a"}</p>
          </div>
        </div>
      ) : null}
      {surpriseOk && hasRange && actual && c ? (
        <div className="flex flex-col gap-1">
          <div
            role="img"
            aria-label={`Actual ${fmt(actual.value)} is ${POSITION_TEXT[s.position].toLowerCase()} of ${fmt(c.low as number)} to ${fmt(c.high as number)}`}
            className="relative h-6"
          >
            <span className="absolute top-2.5 h-1 bg-accent-soft outline outline-1 outline-accent" style={{ left: `${x(c.low as number)}%`, width: `${x(c.high as number) - x(c.low as number)}%` }} />
            <span className="absolute top-1 h-4 w-0.5 bg-accent-ramp-1" style={{ left: `${x(c.value as number)}%` }} />
            <span className="absolute top-0 flex -translate-x-1/2 flex-col items-center" style={{ left: `${x(actual.value)}%` }}>
              <i aria-hidden="true" className="block size-3 rotate-45 bg-text" />
            </span>
          </div>
          <p className="text-xs text-text-muted">
            ◆ actual · bar = mean · band = analyst range {fmt(c.low as number)} to {fmt(c.high as number)}
            {c.analystCount !== null ? `, ${c.analystCount} analysts` : ", analyst count not stated"}. <strong className="font-medium text-text">{POSITION_TEXT[s.position]}.</strong>
          </p>
        </div>
      ) : (
        <p role="status" className="border border-dashed border-border px-3 py-2 text-sm text-text-muted">
          <strong className="font-medium text-text">{status.state === "not_covered" ? "No comparison available." : "Not compared."}</strong> {status.reason} No estimate is created.
        </p>
      )}
      <ProvenanceFooter
        provenance={{
          source: c?.provider ? `${c.provider} (consensus) · filed actual${actual?.accession ? ` ${actual.accession}` : ""}` : `Filed actual${actual?.accession ? ` ${actual.accession}` : ""}`,
          asOf: actual?.knownAt ? actual.knownAt.slice(0, 10) : null,
          knownAt: c?.knownAt ?? null,
          method: `Surprise = actual ÷ consensus mean − 1, on the provider's stated basis${c?.basis ? ` (${c.basis})` : ""}; consensus vintage ${c?.vintage ? formatDate(c.vintage) : "n/a"}`,
          state: status.state,
          reason: status.reason,
        }}
      />
    </article>
  );
}
