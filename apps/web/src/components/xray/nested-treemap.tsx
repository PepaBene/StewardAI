import { squarify } from "@/lib/charts/squarify";
import { SrTable } from "@/components/charts";
import type { ExposureTreemap, TreemapGroup, TreemapLeaf } from "@/lib/xray/exposure";
import { truncate, weightPct } from "@/lib/xray/format";
import { cn } from "@/lib/utils";

const W = 1000;
const H = 540;
const HEADER = 20;
const PAD = 3;

// Class names are written out in full so Tailwind finds them.
const GROUP_FILL = [
  "fill-categorical-1",
  "fill-categorical-2",
  "fill-categorical-3",
  "fill-categorical-4",
  "fill-categorical-5",
  "fill-categorical-6",
  "fill-categorical-7",
] as const;
const GROUP_STROKE = [
  "stroke-categorical-1",
  "stroke-categorical-2",
  "stroke-categorical-3",
  "stroke-categorical-4",
  "stroke-categorical-5",
  "stroke-categorical-6",
  "stroke-categorical-7",
] as const;

type Style = { fill: string; stroke: string; dashed: boolean; leafOpacity: number; headerOpacity: number };

function styleFor(group: TreemapGroup, colourIndex: number): Style {
  switch (group.kind) {
    case "classified": {
      const i = colourIndex % GROUP_FILL.length;
      return { fill: GROUP_FILL[i], stroke: GROUP_STROKE[i], dashed: false, leafOpacity: 0.16, headerOpacity: 0.4 };
    }
    case "cash":
    case "fund_cash":
      return { fill: "fill-chart-benchmark", stroke: "stroke-chart-benchmark", dashed: false, leafOpacity: 0.25, headerOpacity: 0.35 };
    default:
      // unclassified, unknown fund weight, fund not looked through: flagged, dashed.
      return { fill: "fill-warning-signal", stroke: "stroke-warning-signal", dashed: true, leafOpacity: 0.18, headerOpacity: 0.3 };
  }
}

export const GROUP_KIND_LABEL: Record<TreemapGroup["kind"], string> = {
  classified: "classified",
  unclassified: "unclassified",
  cash: "cash",
  fund_cash: "fund cash and derivatives",
  not_looked_through: "fund not looked through",
  unknown_fund: "unknown fund weight",
};

/**
 * Nested treemap: groups (sector / region / country / currency buckets) by weight, issuers
 * nested inside. Area is proportional to the share of TOTAL portfolio value at both levels.
 * Cash, fund cash, funds not looked through and unknown fund weight are their own dashed or
 * grey groups, never merged into an issuer. Every tile is also in the hidden table.
 */
export function NestedTreemap({
  map,
  label,
  className,
}: {
  map: ExposureTreemap;
  label: string;
  className?: string;
}) {
  const groupTiles = squarify(map.groups, (g) => g.weight, { x: 0, y: 0, w: W, h: H });
  // Colour slot per classified group, in layout (largest first) order.
  const colourOf = new Map<string, number>();
  groupTiles.forEach((gt) => {
    if (gt.item.kind === "classified") colourOf.set(gt.item.key, colourOf.size);
  });
  const rows: (readonly string[])[] = groupTiles.flatMap((gt) => [
    [`${gt.item.label} (${GROUP_KIND_LABEL[gt.item.kind]})`, weightPct(gt.item.weight), `${gt.item.children.length} tiles`] as const,
    ...gt.item.children.map((c) => [`${gt.item.label} › ${c.label}`, weightPct(c.weight), c.kind === "remainder" ? "remainder" : "issuer"] as const),
  ]);
  return (
    <figure className={cn("m-0 relative overflow-x-auto", className)}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="block h-auto w-full min-w-[640px]">
        {groupTiles.map((gt) => {
          const g = gt.item;
          const s = styleFor(g, colourOf.get(g.key) ?? 0);
          const gx = gt.x + 1;
          const gy = gt.y + 1;
          const gw = Math.max(0, gt.w - 2);
          const gh = Math.max(0, gt.h - 2);
          const showHeader = gw > 70 && gh > 44;
          const inner = {
            x: gx + PAD,
            y: gy + (showHeader ? HEADER : PAD),
            w: Math.max(0, gw - PAD * 2),
            h: Math.max(0, gh - (showHeader ? HEADER : PAD) - PAD),
          };
          const leaves = squarify<TreemapLeaf>(g.children, (c) => c.weight, inner);
          return (
            <g key={g.key}>
              <title>{`${g.label}: ${weightPct(g.weight)} of portfolio value (${GROUP_KIND_LABEL[g.kind]})`}</title>
              <rect
                x={gx}
                y={gy}
                width={gw}
                height={gh}
                className={cn(s.fill, s.stroke)}
                fillOpacity={s.headerOpacity * 0.5}
                strokeWidth={1.2}
                strokeDasharray={s.dashed ? "5 3" : undefined}
              />
              {showHeader ? (
                <text x={gx + 6} y={gy + 14} fontSize={12} className="fill-text font-semibold">
                  {truncate(g.label, Math.floor((gw - 12) / 7.2) - 6)} {weightPct(g.weight)}
                </text>
              ) : null}
              {leaves.map((lt) => {
                const leaf = lt.item;
                const remainder = leaf.kind === "remainder";
                const roomy = lt.w > 64 && lt.h > 30;
                const tip = `${leaf.label}\n${weightPct(leaf.weight)} of portfolio value · ${leaf.detail}`;
                return (
                  <g key={leaf.id} tabIndex={0} role="img" aria-label={`${g.label}, ${leaf.label}, ${weightPct(leaf.weight)}`} className="outline-hidden focus-visible:[&>rect]:stroke-text">
                    <title>{tip}</title>
                    <rect
                      x={lt.x + 0.75}
                      y={lt.y + 0.75}
                      width={Math.max(0, lt.w - 1.5)}
                      height={Math.max(0, lt.h - 1.5)}
                      className={cn(remainder ? "fill-surface-muted stroke-border" : cn(s.fill, "stroke-surface"))}
                      fillOpacity={remainder ? 1 : s.leafOpacity + 0.12}
                      strokeWidth={remainder ? 1 : 1.5}
                      strokeDasharray={remainder ? "3 3" : undefined}
                    />
                    {roomy ? (
                      <>
                        <text x={lt.x + 6} y={lt.y + 15} fontSize={11} className={cn("font-semibold", remainder ? "fill-text-muted" : "fill-text")}>
                          {truncate(leaf.label, Math.floor((lt.w - 10) / 6.4))}
                        </text>
                        <text x={lt.x + 6} y={lt.y + 28} fontSize={11} className="num fill-text-muted">
                          {weightPct(leaf.weight)}
                        </text>
                      </>
                    ) : null}
                  </g>
                );
              })}
              {g.children.length === 0 && gw > 50 && gh > 30 ? (
                <text x={gx + 6} y={gy + gh - 8} fontSize={11} className="fill-text-muted">
                  {GROUP_KIND_LABEL[g.kind]}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      <SrTable caption={label} columns={["Group or issuer", "Share of portfolio value", "Kind"]} rows={rows} />
    </figure>
  );
}
