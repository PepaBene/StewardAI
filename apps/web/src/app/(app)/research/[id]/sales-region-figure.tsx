import { AXIS_TEXT, CATEGORICAL_BG, CATEGORICAL_FILL, Legend, SrTable } from "@/components/charts/shared";
import { formatNumber } from "@/lib/charts/format";
import { bandScale, linearScale } from "@/lib/charts/scale";
import { AS_REPORTED_NOTE, type SalesRegionFigure } from "@/lib/reference/segments";

const W = 400;
const H = 200;
const PAD = { top: 16, bottom: 20 };

/**
 * Sales by region per period as stacked columns (largest regions of the latest period by
 * name, the rest grouped), from the company's SEC XBRL filings as it reports them. The
 * total sits on each column; the values are in the reporting currency.
 */
export function SalesRegionFigureView({ figure, number }: { figure: SalesRegionFigure; number: number }) {
  const { columns, names, divisor, unitLabel } = figure;
  const fmt = (v: number) => formatNumber(v / divisor, v / divisor >= 100 ? 0 : 1);
  const max = Math.max(...columns.map((c) => c.total));
  const y = linearScale([0, max * 1.08], [H - PAD.bottom, PAD.top]);
  const band = bandScale(columns.length, [0, W], { paddingInner: 0.3, maxBandwidth: 64 });
  const slot = (name: string) => Math.min(names.indexOf(name), 7);
  const title = `Fig. ${number} · Sales by region${unitLabel ? `, ${unitLabel}` : ""}`;

  return (
    <figure className="m-0 flex flex-col gap-2.5">
      <figcaption className="font-heading text-lg font-semibold">{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full overflow-visible" aria-hidden="true">
        {columns.map((c, i) => {
          const x = band.x(i);
          let base = H - PAD.bottom;
          return (
            <g key={c.label}>
              {c.parts.map((p) => {
                const h = Math.max(0, H - PAD.bottom - y(p.value));
                base -= h;
                return p.value > 0 ? (
                  <rect key={p.name} x={x} y={base} width={band.bandwidth} height={Math.max(0, h - 1)} className={CATEGORICAL_FILL[slot(p.name)]}>
                    <title>{`${c.label} · ${p.name}: ${fmt(p.value)} ${unitLabel}`}</title>
                  </rect>
                ) : null;
              })}
              <text x={x + band.bandwidth / 2} y={y(c.total) - 4} textAnchor="middle" fontSize={10} className="fill-text-muted num">
                {fmt(c.total)}
              </text>
              <text x={x + band.bandwidth / 2} y={H - 6} textAnchor="middle" fontSize={10} className={AXIS_TEXT}>
                {c.label}
              </text>
            </g>
          );
        })}
        <line x1={0} x2={W} y1={H - PAD.bottom} y2={H - PAD.bottom} className="stroke-border" strokeWidth={1} />
      </svg>
      <Legend items={names.map((n) => ({ label: n, className: CATEGORICAL_BG[slot(n)] }))} />
      <p className="text-xs text-text-muted">
        {figure.source ? `Source: ${figure.source}. ` : ""}
        {AS_REPORTED_NOTE}
      </p>
      <SrTable
        caption={title}
        columns={["Period", ...names, "Total"]}
        rows={columns.map((c) => [c.label, ...c.parts.map((p) => fmt(p.value)), fmt(c.total)])}
      />
    </figure>
  );
}
