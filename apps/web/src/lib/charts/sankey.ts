/*
 * Column sankey layout: nodes in fixed columns (e.g. sources -> "Available" -> uses), node
 * heights and ribbon widths proportional to value on one shared scale.
 */

import { n } from "./path";

export type SankeyNodeInput = { id: string; label: string; column: number };
export type SankeyLinkInput = { source: string; target: string; value: number; emphasis?: boolean };

export type SankeyNode = SankeyNodeInput & {
  /** max(total in, total out). */
  value: number;
  /** 0-based position among the non-empty columns. */
  columnIndex: number;
  x: number;
  y: number;
  h: number;
};

export type SankeyRibbon = {
  source: string;
  target: string;
  value: number;
  emphasis: boolean;
  /** Ribbon thickness. */
  width: number;
  d: string;
};

export type SankeyLayout = { nodes: SankeyNode[]; ribbons: SankeyRibbon[]; columns: number };

/**
 * Lays out the graph in a `width` × `height` box. Links with a non-positive value or an
 * unknown endpoint are ignored, and nodes left with no flow are dropped. Nodes keep their
 * input order within a column; each column is centred vertically. Returns null when there
 * is no flow to draw.
 */
export function sankeyLayout(
  nodesIn: readonly SankeyNodeInput[],
  linksIn: readonly SankeyLinkInput[],
  { width, height, nodeWidth = 10, gap = 16 }: { width: number; height: number; nodeWidth?: number; gap?: number },
): SankeyLayout | null {
  const byId = new Map(nodesIn.map((node) => [node.id, node]));
  const links = linksIn.filter(
    (l) => Number.isFinite(l.value) && l.value > 0 && byId.has(l.source) && byId.has(l.target) && l.source !== l.target,
  );
  if (links.length === 0) return null;

  const inSum = new Map<string, number>();
  const outSum = new Map<string, number>();
  for (const l of links) {
    outSum.set(l.source, (outSum.get(l.source) ?? 0) + l.value);
    inSum.set(l.target, (inSum.get(l.target) ?? 0) + l.value);
  }
  const live = nodesIn
    .map((node) => ({ node, value: Math.max(inSum.get(node.id) ?? 0, outSum.get(node.id) ?? 0) }))
    .filter((e) => e.value > 0);

  const columnKeys = [...new Set(live.map((e) => e.node.column))].sort((a, b) => a - b);
  const columns = columnKeys.map((c) => live.filter((e) => e.node.column === c));
  const scale = Math.min(
    ...columns.map((col) => {
      const total = col.reduce((s, e) => s + e.value, 0);
      return Math.max(0, height - gap * (col.length - 1)) / total;
    }),
  );

  const placed = new Map<string, SankeyNode>();
  columns.forEach((col, ci) => {
    const x = columns.length > 1 ? (ci * (width - nodeWidth)) / (columns.length - 1) : 0;
    const used = col.reduce((s, e) => s + e.value * scale, 0) + gap * (col.length - 1);
    let y = (height - used) / 2;
    for (const { node, value } of col) {
      const h = value * scale;
      placed.set(node.id, { ...node, value, columnIndex: ci, x, y, h });
      y += h + gap;
    }
  });

  // Stack ribbons at each end in the order of the node at the other end (fewer crossings).
  const outOffset = new Map<string, number>();
  const inOffset = new Map<string, number>();
  const ribbonY0 = new Map<SankeyLinkInput, number>();
  const ribbonY1 = new Map<SankeyLinkInput, number>();
  const centre = (id: string) => placed.get(id)!.y;
  for (const l of [...links].sort((a, b) => centre(a.target) - centre(b.target))) {
    const src = placed.get(l.source)!;
    const off = outOffset.get(l.source) ?? 0;
    ribbonY0.set(l, src.y + off);
    outOffset.set(l.source, off + l.value * scale);
  }
  for (const l of [...links].sort((a, b) => centre(a.source) - centre(b.source))) {
    const tgt = placed.get(l.target)!;
    const off = inOffset.get(l.target) ?? 0;
    ribbonY1.set(l, tgt.y + off);
    inOffset.set(l.target, off + l.value * scale);
  }

  const ribbons: SankeyRibbon[] = links.map((l) => {
    const src = placed.get(l.source)!;
    const tgt = placed.get(l.target)!;
    const t = l.value * scale;
    const y0 = ribbonY0.get(l)!;
    const y1 = ribbonY1.get(l)!;
    const x0 = src.x + nodeWidth;
    const x1 = tgt.x;
    const m = (x0 + x1) / 2;
    const d =
      `M${n(x0)} ${n(y0)} C${n(m)} ${n(y0)} ${n(m)} ${n(y1)} ${n(x1)} ${n(y1)} ` +
      `L${n(x1)} ${n(y1 + t)} C${n(m)} ${n(y1 + t)} ${n(m)} ${n(y0 + t)} ${n(x0)} ${n(y0 + t)} Z`;
    return { source: l.source, target: l.target, value: l.value, emphasis: !!l.emphasis, width: t, d };
  });

  return { nodes: [...placed.values()], ribbons, columns: columns.length };
}

/**
 * Spreads label anchor positions so neighbours are at least `minGap` apart while staying
 * within [lo, hi] and as close to their `targets` as possible. Order is preserved. Used to
 * stop small sankey nodes' labels from overprinting each other.
 */
export function spreadLabels(targets: readonly number[], minGap: number, lo = -Infinity, hi = Infinity): number[] {
  const order = targets.map((t, i) => ({ t, i })).sort((a, b) => a.t - b.t);
  const pos = order.map((o) => Math.min(hi, Math.max(lo, o.t)));
  for (let k = 1; k < pos.length; k++) pos[k] = Math.max(pos[k], pos[k - 1] + minGap);
  if (pos.length && pos[pos.length - 1] > hi) {
    pos[pos.length - 1] = hi;
    for (let k = pos.length - 2; k >= 0; k--) pos[k] = Math.min(pos[k], pos[k + 1] - minGap);
  }
  const out = Array<number>(targets.length);
  order.forEach((o, k) => (out[o.i] = pos[k]));
  return out;
}
