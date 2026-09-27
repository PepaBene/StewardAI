import { formatCompact } from "@/lib/charts/format";
import {
  sankeyLayout,
  spreadLabels,
  type SankeyLinkInput,
  type SankeyNode,
  type SankeyNodeInput,
} from "@/lib/charts/sankey";
import { cn } from "@/lib/utils";

import { ChartEmpty, SrTable, SVG_BASE } from "./shared";

export type { SankeyLinkInput, SankeyNodeInput } from "@/lib/charts/sankey";

export type SankeyProps = {
  /** Nodes with their column (0 = sources, 1 = the middle node, 2 = targets, …). */
  nodes: readonly SankeyNodeInput[];
  /** Flows; `emphasis` draws the ribbon in the accent, others in a faint ink wash. */
  links: readonly SankeyLinkInput[];
  /** viewBox size; the SVG scales to its container width. */
  width?: number;
  height?: number;
  /** Space reserved left of the first column for its labels. Default 110. */
  labelWidth?: number;
  nodeWidth?: number;
  /** Vertical gap between nodes in a column. Default 18. */
  gap?: number;
  formatValue?: (value: number) => string;
  /** Accessible summary, e.g. "Cash flow year to date". */
  label?: string;
  className?: string;
};

/** Minimum distance between two label anchors (name line + value line + air). */
const LABEL_GAP = 30;

/**
 * Flow diagram (sources → middle → targets): node heights and ribbon widths share one
 * scale. First-column labels sit left of their node, the rest to the right; labels of small
 * neighbouring nodes are spread apart and tied back with a leader line. Every flow is also
 * in a visually hidden table.
 */
export function Sankey({
  nodes,
  links,
  width = 790,
  height = 270,
  labelWidth = 110,
  nodeWidth = 10,
  gap = 18,
  formatValue = formatCompact,
  label = "Flows",
  className,
}: SankeyProps) {
  const rightPad = 110;
  const innerW = width - labelWidth - rightPad;
  const layout = sankeyLayout(nodes, links, { width: innerW, height: height - 12, nodeWidth, gap });
  if (!layout) return <ChartEmpty minHeight={height / 2} className={className} />;

  const byId = new Map(layout.nodes.map((node) => [node.id, node]));
  const labelY = new Map<string, number>();
  for (let c = 0; c < layout.columns; c++) {
    const col = layout.nodes.filter((node) => node.columnIndex === c);
    const spread = spreadLabels(
      col.map((node) => node.y + node.h / 2),
      LABEL_GAP,
      10,
      height - 12 - 14,
    );
    col.forEach((node, i) => labelY.set(node.id, spread[i]));
  }

  const nameOf = (id: string) => byId.get(id)?.label ?? id;
  const summary =
    `${label}: ` +
    layout.nodes
      .filter((node) => node.columnIndex === 0)
      .map((node) => `${node.label} ${formatValue(node.value)}`)
      .join(", ") +
    " → " +
    layout.nodes
      .filter((node) => node.columnIndex === layout.columns - 1)
      .map((node) => `${node.label} ${formatValue(node.value)}`)
      .join(", ");

  const labelFor = (node: SankeyNode) => {
    const first = node.columnIndex === 0;
    const centre = node.y + node.h / 2;
    const ly = labelY.get(node.id) ?? centre;
    const lx = first ? node.x - 8 : node.x + nodeWidth + 8;
    const moved = Math.abs(ly - centre) > 4;
    return (
      <g key={`l-${node.id}`}>
        {moved ? (
          <line
            x1={first ? node.x - 1 : node.x + nodeWidth + 1}
            x2={first ? lx + 2 : lx - 2}
            y1={centre}
            y2={ly}
            className="stroke-border"
            strokeWidth={1}
          />
        ) : null}
        <text x={lx} y={ly - 2} textAnchor={first ? "end" : "start"} fontSize={12} className="fill-text font-semibold">
          {node.label}
        </text>
        <text x={lx} y={ly + 11} textAnchor={first ? "end" : "start"} fontSize={11} className="fill-text-muted num">
          {formatValue(node.value)}
        </text>
      </g>
    );
  };

  return (
    <figure className={cn("m-0", className)}>
      <svg viewBox={`0 0 ${width} ${height}`} className={SVG_BASE} role="img" aria-label={summary}>
        <g transform={`translate(${labelWidth},6)`}>
          {layout.ribbons.map((r, i) => (
            <path
              key={i}
              d={r.d}
              className={cn(r.emphasis ? "fill-accent" : "fill-text", "transition-opacity")}
              fillOpacity={r.emphasis ? 0.28 : 0.07}
            >
              <title>{`${nameOf(r.source)} → ${nameOf(r.target)}: ${formatValue(r.value)}`}</title>
            </path>
          ))}
          {layout.nodes.map((node) => (
            <rect key={node.id} x={node.x} y={node.y} width={nodeWidth} height={Math.max(1, node.h)} className="fill-accent">
              <title>{`${node.label}: ${formatValue(node.value)}`}</title>
            </rect>
          ))}
          {layout.nodes.map(labelFor)}
        </g>
      </svg>
      <SrTable
        caption={label}
        columns={["From", "To", "Amount"]}
        rows={layout.ribbons.map((r) => [nameOf(r.source), nameOf(r.target), formatValue(r.value)])}
      />
    </figure>
  );
}
