import { hasCompleteOhlc, hasVolume, type OhlcBar } from "@/lib/charts/candles";
import { formatCompact, formatNumber } from "@/lib/charts/format";
import { areaPath, linePath, n, type Point } from "@/lib/charts/path";
import { bandScale, extent, isFiniteNumber, linearScale, niceTicks } from "@/lib/charts/scale";
import { movingAverage } from "@/lib/charts/series";
import { formatDay, timeTicks, type TimeTick } from "@/lib/charts/time";
import { cn } from "@/lib/utils";

import { AXIS_TEXT, ChartEmpty, Legend, SVG_BASE, type LegendEntry } from "./shared";

export type { OhlcBar } from "@/lib/charts/candles";

export type CandleChartProps = {
  /** Daily bars, oldest first. */
  bars: readonly OhlcBar[];
  /** Instrument name for the accessible summary, e.g. "ASML". */
  label?: string;
  /** Simple moving average window over closes (e.g. 20); omit for none. */
  maWindow?: number;
  /** Volume bars under the price pane (when any bar has volume). Default true. */
  volume?: boolean;
  /** Dashed line + tag at the last close. Default true. */
  lastClose?: boolean;
  /** viewBox width; the SVG scales to its container. */
  width?: number;
  /** Height of the price pane; the volume pane and x labels add below. */
  height?: number;
  volumeHeight?: number;
  formatPrice?: (value: number) => string;
  /** X-axis labels in place of the month ticks (e.g. hours for 5-minute bars). */
  xTicks?: readonly TimeTick[];
  ariaLabel?: string;
  className?: string;
};

/**
 * OHLC candles with an optional moving-average line and volume bars beneath. Up candles are
 * solid gain, down candles loss-outlined (shape differs, not only hue). When any priced bar
 * lacks open/high/low the whole series falls back to a close-price line, marked "Close only".
 */
export function CandleChart({
  bars,
  label = "Price",
  maWindow,
  volume = true,
  lastClose = true,
  width = 880,
  height = 280,
  volumeHeight = 56,
  formatPrice = (v) => formatNumber(v, 2),
  xTicks,
  ariaLabel,
  className,
}: CandleChartProps) {
  const closes = bars.map((b) => (isFiniteNumber(b.close) ? b.close : null));
  const finiteCloses = closes.filter(isFiniteNumber);
  if (finiteCloses.length < 2) return <ChartEmpty minHeight={height / 2} className={className} />;

  const candles = hasCompleteOhlc(bars);
  const showVolume = volume && hasVolume(bars);
  const ma = maWindow && maWindow > 1 ? movingAverage(closes, maWindow) : null;
  const hasMa = !!ma && ma.some(isFiniteNumber);

  const right = 52;
  const x0 = 0;
  const x1 = width - right;
  const priceTop = 8;
  const priceBottom = height;
  const volTop = priceBottom + 12;
  const volBottom = showVolume ? volTop + volumeHeight : priceBottom;
  const total = volBottom + 22;

  const lows = candles ? bars.map((b) => b.low ?? null) : closes;
  const highs = candles ? bars.map((b) => b.high ?? null) : closes;
  const ext = extent([...lows, ...highs, ...(hasMa ? ma! : [])])!;
  // Tight domain (data ±3% of its range) so candles use the pane; round ticks inside it.
  const pad = (ext[1] - ext[0] || Math.abs(ext[1]) || 1) * 0.03;
  const domain: [number, number] = [ext[0] - pad, ext[1] + pad];
  const ticks = niceTicks(domain[0], domain[1], 5).filter((t) => t >= domain[0] && t <= domain[1]);
  const y = linearScale(domain, [priceBottom, priceTop]);
  const band = bandScale(bars.length, [x0, x1], { paddingInner: 0.36 });
  const cx = (i: number) => band.x(i) + band.bandwidth / 2;
  const maxVol = Math.max(0, ...bars.map((b) => (isFiniteNumber(b.volume) ? b.volume : 0)));
  const vy = linearScale([0, maxVol || 1], [volBottom, volTop]);

  const lastIdx = closes.map((c, i) => (isFiniteNumber(c) ? i : -1)).filter((i) => i >= 0).pop()!;
  const last = closes[lastIdx]!;
  const first = finiteCloses[0];
  const lo = Math.min(...finiteCloses);
  const hi = Math.max(...finiteCloses);

  const summary =
    ariaLabel ??
    `${label}, ${formatDay(bars[0].date)} to ${formatDay(bars[bars.length - 1].date)}: ` +
      `close ${formatPrice(first)} to ${formatPrice(last)}, range ${formatPrice(lo)}–${formatPrice(hi)}` +
      (candles ? "" : " (close prices only; open, high and low not available)");

  const closePts: (Point | null)[] = closes.map((c, i) => (isFiniteNumber(c) ? [cx(i), y(c)] : null));
  const maPts: (Point | null)[] | null = hasMa ? ma!.map((v, i) => (isFiniteNumber(v) ? [cx(i), y(v)] : null)) : null;

  const legendItems: LegendEntry[] = [
    ...(candles
      ? [
          { label: "Up day", swatch: "square", className: "bg-gain" } as const,
          { label: "Down day", swatch: "outline", className: "border-loss bg-loss-subtle" } as const,
        ]
      : hasMa
        ? [{ label: "Close", swatch: "line", className: "bg-chart-line" } as const]
        : []),
    ...(hasMa ? [{ label: `MA ${maWindow}`, swatch: "line", className: "bg-text" } as const] : []),
  ];

  return (
    <figure className={cn("m-0 flex flex-col gap-2", className)}>
      <svg viewBox={`0 0 ${width} ${total}`} className={SVG_BASE} role="img" aria-label={summary}>
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={x0}
              x2={x1}
              y1={y(t)}
              y2={y(t)}
              className="stroke-chart-grid"
              strokeWidth={1}
              shapeRendering="crispEdges"
            />
            <text x={width} y={y(t) + 4} textAnchor="end" fontSize={11} className={AXIS_TEXT}>
              {formatNumber(t, Number.isInteger(t) ? 0 : 2)}
            </text>
          </g>
        ))}

        {candles ? (
          bars.map((b, i) => {
            if (!isFiniteNumber(b.close)) return null;
            const o = b.open!;
            const c = b.close;
            const up = c >= o;
            const top = y(Math.max(o, c));
            const bodyH = Math.max(1, y(Math.min(o, c)) - top);
            return (
              <g key={i} className={up ? "stroke-gain" : "stroke-loss"}>
                <line x1={cx(i)} x2={cx(i)} y1={y(b.high!)} y2={y(b.low!)} strokeWidth={1} />
                <rect
                  x={band.x(i)}
                  y={top}
                  width={band.bandwidth}
                  height={bodyH}
                  className={up ? "fill-gain" : "fill-loss-subtle"}
                  strokeWidth={1}
                />
              </g>
            );
          })
        ) : (
          <>
            <path d={areaPath(closePts, priceBottom)} className="fill-chart-line" fillOpacity={0.08} />
            <path
              d={linePath(closePts)}
              fill="none"
              className="stroke-chart-line"
              strokeWidth={2}
              strokeLinejoin="round"
            />
            <text x={4} y={priceTop + 12} fontSize={11} className={AXIS_TEXT}>
              Close only · no OHLC data
            </text>
          </>
        )}

        {maPts ? (
          <path d={linePath(maPts)} fill="none" className="stroke-text-muted" strokeWidth={1.5} strokeLinejoin="round" />
        ) : null}

        {lastClose ? (
          <g>
            <line
              x1={x0}
              x2={x1}
              y1={y(last)}
              y2={y(last)}
              className="stroke-accent"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <rect x={x1 + 2} y={y(last) - 9} width={right - 2} height={18} className="fill-accent" />
            <text
              x={width - 4}
              y={y(last) + 4}
              textAnchor="end"
              fontSize={11}
              className="fill-accent-foreground font-mono num"
            >
              {formatNumber(last, last >= 1000 ? 0 : 2)}
            </text>
          </g>
        ) : null}

        {showVolume
          ? bars.map((b, i) => {
              if (!isFiniteNumber(b.volume) || b.volume <= 0) return null;
              // Volume is neutral (chart-track), never up/down coloured: the candles already say so.
              return (
                <rect
                  key={i}
                  x={band.x(i)}
                  y={vy(b.volume)}
                  width={band.bandwidth}
                  height={volBottom - vy(b.volume)}
                  className="fill-chart-track"
                />
              );
            })
          : null}

        {(xTicks ?? timeTicks(bars.map((b) => b.date))).map((t) => (
          <text key={t.index} x={n(cx(t.index))} y={total - 6} fontSize={11} className={AXIS_TEXT}>
            {t.label}
          </text>
        ))}

        <g>
          {bars.map((b, i) => (
            <rect
              key={i}
              x={x0 + i * band.step}
              y={priceTop}
              width={band.step}
              height={volBottom - priceTop}
              className="fill-text opacity-0 hover:opacity-[0.05]"
            >
              <title>
                {`${formatDay(b.date)} · ` +
                  (candles && isFiniteNumber(b.close)
                    ? `O ${formatPrice(b.open!)} H ${formatPrice(b.high!)} L ${formatPrice(b.low!)} C ${formatPrice(b.close)}`
                    : `Close ${isFiniteNumber(b.close) ? formatPrice(b.close) : "no data"}`) +
                  (isFiniteNumber(b.volume) ? ` · Vol ${formatCompact(b.volume)}` : "") +
                  (hasMa && isFiniteNumber(ma![i]) ? ` · MA ${formatPrice(ma![i]!)}` : "")}
              </title>
            </rect>
          ))}
        </g>
      </svg>
      {legendItems.length ? <Legend items={legendItems} /> : null}
    </figure>
  );
}
