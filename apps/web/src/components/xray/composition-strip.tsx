import { formatPercent } from "@/lib/format";
import { compositionSegments, segmentTone, type XrayBucket } from "@/lib/xray/xray";
import { cn } from "@/lib/utils";

/**
 * The whole portfolio as one bar, split by the chosen lens. It always spans 100%: cash is a flat
 * grey segment and weight the app cannot place is hatched, so the unknown part is as visible as
 * any holding. Each segment is named in the legend with its weight; colour is never the only cue.
 */
export function CompositionStrip({ buckets, label }: { buckets: readonly XrayBucket[]; label: string }) {
  const segments = compositionSegments(buckets);
  const total = segments.reduce((sum, s) => sum + s.weight, 0);
  if (total <= 0) return null;
  return (
    <figure className="m-0">
      <div
        role="img"
        aria-label={`${label}: ${segments.map((s) => `${s.label} ${formatPercent(s.weight, { decimals: 1 })}`).join(", ")}`}
        className="flex h-9 w-full gap-px overflow-hidden border border-border bg-surface"
      >
        {segments.map((s) => {
          const tone = segmentTone(s);
          return (
            <div
              key={s.key}
              title={`${s.label}: ${formatPercent(s.weight, { decimals: 1 })}`}
              className={cn("h-full min-w-[3px]", tone.className)}
              style={{ width: `${(s.weight / total) * 100}%` }}
            />
          );
        })}
      </div>
      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
        {segments.map((s) => {
          const tone = segmentTone(s);
          return (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <i aria-hidden="true" className={cn("inline-block size-2.5 border border-border", tone.className)} />
              <span>{s.label}</span>
              <span className="num text-text">{formatPercent(s.weight, { decimals: 1 })}</span>
            </span>
          );
        })}
        {Math.abs(total - 1) > 0.005 ? (
          <span className="basis-full">
            The shares add up to {formatPercent(total, { decimals: 1 })}, so each bar is drawn as its part of that total.
          </span>
        ) : null}
      </figcaption>
    </figure>
  );
}
