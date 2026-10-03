import { SrTable } from "@/components/charts";
import { sankeyLayout, spreadLabels, type SankeyNode } from "@/lib/charts/sankey";
import type { LookThroughSankey, SankeyKind } from "@/lib/xray/exposure";
import { truncate, weightPct } from "@/lib/xray/format";
import { cn } from "@/lib/utils";

const W = 1000;
const H = 460;
const LABEL_L = 96;
const LABEL_R = 270;
const NODE_W = 10;
const TOP = 22;

type Tone = { node: string; ribbon: string; opacity: number };

const TONES: Record<"known" | "neutral" | "flag", Tone> = {
  known: { node: "fill-accent", ribbon: "fill-text", opacity: 0.09 },
  neutral: { node: "fill-chart-benchmark", ribbon: "fill-chart-benchmark", opacity: 0.28 },
  flag: { node: "fill-warning-signal", ribbon: "fill-warning-signal", opacity: 0.3 },
};

function toneOf(kind: SankeyKind | undefined): Tone {
  switch (kind) {
    case "cash":
    case "cash_source":
    case "fund_cash":
    case "other_issuers":
    case "rest_of_fund":
      return TONES.neutral;
    case "unknown":
    case "not_looked_through":
      return TONES.flag;
    default:
      return TONES.known;
  }
}

export const SANKEY_LEGEND = [
  { label: "Known holdings / named issuers", swatch: "square" as const, className: "bg-accent" },
  { label: "Cash, fund cash and derivatives, other issuers", swatch: "square" as const, className: "bg-chart-benchmark" },
  { label: "Unknown or not looked through (never redistributed)", swatch: "square" as const, className: "bg-warning-signal" },
];

/**
 * Portfolio position -> fund holdings (dated) -> underlying issuer, ribbon width = share of
 * TOTAL portfolio value. Remainders (cash, the funds' own cash/derivatives, funds not looked
 * through, unknown weight, other issuers) are nodes of their own with their own colour.
 */
export function LookThroughSankeyChart({ sankey, label, className }: { sankey: LookThroughSankey; label: string; className?: string }) {
  const layout = sankeyLayout(sankey.nodes, sankey.links, {
    width: W - LABEL_L - LABEL_R,
    height: H - TOP - 6,
    nodeWidth: NODE_W,
    gap: 12,
  });
  if (!layout) return null;
  const byId = new Map(layout.nodes.map((n) => [n.id, n]));
  const nameOf = (id: string) => byId.get(id)?.label ?? id;

  const labelY = new Map<string, number>();
  for (let c = 0; c < layout.columns; c++) {
    const col = layout.nodes.filter((n) => n.columnIndex === c);
    const spread = spreadLabels(
      col.map((n) => n.y + n.h / 2),
      28,
      12,
      H - TOP - 20,
    );
    col.forEach((n, i) => labelY.set(n.id, spread[i]));
  }
  const headings = ["Position", "Fund holdings (as of)", "Underlying issuer or remainder"];
  const colX = (c: number) => (layout.columns > 1 ? (c * (W - LABEL_L - LABEL_R - NODE_W)) / (layout.columns - 1) : 0);

  const renderLabel = (n: SankeyNode) => {
    const first = n.columnIndex === 0;
    const centre = n.y + n.h / 2;
    const ly = labelY.get(n.id) ?? centre;
    const lx = first ? n.x - 8 : n.x + NODE_W + 8;
    const max = first ? 14 : n.columnIndex === layout.columns - 1 ? 40 : 34;
    return (
      <g key={`l-${n.id}`}>
        {Math.abs(ly - centre) > 4 ? (
          <line x1={first ? n.x - 1 : n.x + NODE_W + 1} x2={first ? lx + 2 : lx - 2} y1={centre} y2={ly} className="stroke-border" strokeWidth={1} />
        ) : null}
        <text
          x={lx}
          y={ly - 2}
          textAnchor={first ? "end" : "start"}
          fontSize={12}
          className="fill-text font-semibold stroke-surface"
          strokeWidth={3}
          paintOrder="stroke"
        >
          {truncate(n.label, max)}
        </text>
        <text x={lx} y={ly + 11} textAnchor={first ? "end" : "start"} fontSize={11} className="num fill-text-muted stroke-surface" strokeWidth={3} paintOrder="stroke">
          {weightPct(n.value)}
        </text>
      </g>
    );
  };

  return (
    <figure className={cn("m-0 relative overflow-x-auto", className)}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="block h-auto w-full min-w-[720px]">
        {headings.slice(0, layout.columns).map((h, c) => (
          <text
            key={h}
            x={LABEL_L + colX(c) + (c === 0 ? 0 : NODE_W)}
            y={12}
            fontSize={11}
            textAnchor={c === 0 ? "start" : "start"}
            className="fill-text-faint font-mono uppercase"
            letterSpacing="0.06em"
          >
            {h}
          </text>
        ))}
        <g transform={`translate(${LABEL_L},${TOP})`}>
          {layout.ribbons.map((r, i) => {
            const t = toneOf(sankey.kinds[r.target]);
            return (
              <path key={i} d={r.d} className={t.ribbon} fillOpacity={t.opacity}>
                <title>{`${nameOf(r.source)} → ${nameOf(r.target)}: ${weightPct(r.value)} of portfolio value`}</title>
              </path>
            );
          })}
          {layout.nodes.map((n) => (
            <rect key={n.id} x={n.x} y={n.y} width={NODE_W} height={Math.max(1, n.h)} className={toneOf(sankey.kinds[n.id]).node}>
              <title>{`${n.label}: ${weightPct(n.value)} of portfolio value`}</title>
            </rect>
          ))}
          {layout.nodes.map(renderLabel)}
        </g>
      </svg>
      <SrTable
        caption={label}
        columns={["From", "To", "Share of portfolio value"]}
        rows={layout.ribbons.map((r) => [nameOf(r.source), nameOf(r.target), weightPct(r.value)])}
      />
    </figure>
  );
}
