import { DataStateBadge } from "@/components/data-state";
import { ExternalLinkIcon } from "lucide-react";

import { geographyName, outsideRange, pointChange, rangePosition } from "@/lib/macro/logic";
import type { IndicatorPoint } from "@/lib/macro/types";

import { day, formatChange, formatFigure } from "./format";

/** A marker on the range strip: a pointer-free tick at `pos` (0..1). */
function Tick({ pos, className, label }: { pos: number; className: string; label: string }) {
  return <span aria-hidden="true" title={label} className={`absolute -inset-y-1 w-0.5 -translate-x-1/2 ${className}`} style={{ left: `${pos * 100}%` }} />;
}

/**
 * One country series at its latest known period, with the previous period and where the value
 * sits inside its 5- and 10-year range. The API returns this one point, not a history, so the
 * card draws exactly that and says so; there is no first-release value to switch to.
 */
export function IndicatorCard({ point }: { point: IndicatorPoint }) {
  const outer = point.tenYear ?? point.fiveYear;
  const outerLabel = point.tenYear ? "10-year" : "5-year";
  const pos = rangePosition(point.value, outer);
  const prevPos = rangePosition(point.previousValue, outer);
  const inner = point.tenYear && point.fiveYear && outer ? point.fiveYear : null;
  const span = outer ? outer.high - outer.low : 0;
  const innerLeft = inner && span > 0 ? Math.max(0, (inner.low - outer!.low) / span) : 0;
  const innerWidth = inner && span > 0 ? Math.min(1, (inner.high - outer!.low) / span) - innerLeft : 0;
  const change = pointChange(point);
  const odd = outsideRange(point.value, outer);

  const aria =
    point.value === null
      ? `${point.seriesId}, ${geographyName(point.geography)}: no value for ${day(point.period)}`
      : `${point.seriesId}, ${geographyName(point.geography)}: ${formatFigure(point.value)} ${point.unit} for ${day(point.period)}${
          pos !== null ? `, at ${Math.round(pos * 100)}% of its ${outerLabel} range` : ", range not available"
        }`;

  return (
    <figure className="m-0 flex min-w-0 flex-col gap-2 border border-border-soft p-3" data-series={point.seriesId}>
      <figcaption className="flex flex-wrap items-start justify-between gap-2">
        <span className="min-w-0">
          <span className="block font-heading text-[17px] leading-tight font-semibold break-words">{point.seriesId}</span>
          <span className="block text-xs text-text-muted">
            {geographyName(point.geography)} · {point.source} · {point.unit}
          </span>
        </span>
        <span className="text-right">
          <span className="num block font-heading text-[24px] leading-none font-semibold">{formatFigure(point.value)}</span>
          <span className="num block text-[11px] text-text-muted">{day(point.period)}</span>
        </span>
      </figcaption>

      <div role="img" aria-label={aria} className="flex flex-col gap-1">
        {outer ? (
          <>
            <div className="relative mt-1 h-2 bg-accent-soft">
              {inner ? <span aria-hidden="true" className="absolute inset-y-0 bg-accent-300" style={{ left: `${innerLeft * 100}%`, width: `${innerWidth * 100}%` }} /> : null}
              {prevPos !== null ? <Tick pos={prevPos} className="border-x border-text bg-surface" label={`Previous period ${formatFigure(point.previousValue)}`} /> : null}
              {pos !== null ? <Tick pos={pos} className="bg-text" label={`Latest ${formatFigure(point.value)}`} /> : null}
            </div>
            <div className="num flex justify-between text-[11px] text-text-muted">
              <span>{formatFigure(outer.low)}</span>
              <span>{outerLabel} range</span>
              <span>{formatFigure(outer.high)}</span>
            </div>
          </>
        ) : (
          <p className="text-xs text-text-muted">No 5- or 10-year range is available for this series.</p>
        )}
      </div>

      <dl className="num m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px] text-text-muted">
        <dt>Previous</dt>
        <dd className="m-0 text-text">
          {point.previousValue !== null ? `${formatFigure(point.previousValue)} (${day(point.previousPeriod)})` : "no earlier period known"}
        </dd>
        <dt>Change</dt>
        <dd className="m-0 text-text">{change !== null ? `${formatChange(change)} ${point.unit}` : "not stated"}</dd>
        {inner ? (
          <>
            <dt>5-year range</dt>
            <dd className="m-0 text-text">
              {formatFigure(inner.low)} to {formatFigure(inner.high)}
            </dd>
          </>
        ) : null}
        <dt>Released</dt>
        <dd className="m-0 text-text">{point.releaseAt ? day(point.releaseAt) : "release date not recorded"}</dd>
        <dt>Known since</dt>
        <dd className="m-0 text-text">{day(point.knownAt)}</dd>
      </dl>
      {odd ? <p className="text-[11px] text-text">The value lies outside the stored range; treat the range as incomplete.</p> : null}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-muted">
        <DataStateBadge state={point.state} reason={point.stateReason} />
        <a href={point.sourceUrl} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 text-accent-ink hover:underline">
          Source
          <ExternalLinkIcon aria-hidden="true" className="size-3" />
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      </div>
    </figure>
  );
}
