/*
 * Squarified treemap layout (Bruls, Huijsing & van Wijk, 2000): tiles keep aspect ratios
 * close to 1 so small weights stay readable. Items are laid out largest first.
 */

export type Rect = { x: number; y: number; w: number; h: number };
export type Tile<T> = Rect & { item: T; index: number; weight: number };

/** Worst (largest) aspect ratio in a row of `areas` laid along a side of length `side`. */
function worst(areas: readonly number[], side: number): number {
  const sum = areas.reduce((s, a) => s + a, 0);
  if (sum <= 0 || side <= 0) return Infinity;
  let max = 0;
  for (const a of areas) {
    const r = Math.max((side * side * a) / (sum * sum), (sum * sum) / (side * side * a));
    if (r > max) max = r;
  }
  return max;
}

/**
 * Lays out `items` in `rect`, each tile's area proportional to `weight(item)`. Items with a
 * zero, negative or non-finite weight get no tile. `index` is the item's position in the
 * input array (tiles come back largest first).
 */
export function squarify<T>(items: readonly T[], weight: (item: T) => number, rect: Rect): Tile<T>[] {
  const entries = items
    .map((item, index) => ({ item, index, weight: weight(item) }))
    .filter((e) => Number.isFinite(e.weight) && e.weight > 0)
    .sort((a, b) => b.weight - a.weight || a.index - b.index);
  const total = entries.reduce((s, e) => s + e.weight, 0);
  if (total <= 0 || rect.w <= 0 || rect.h <= 0) return [];

  const scale = (rect.w * rect.h) / total;
  const areas = entries.map((e) => e.weight * scale);
  const tiles: Tile<T>[] = [];
  let { x, y, w, h } = rect;

  const layoutRow = (row: number[]) => {
    const sum = row.reduce((s, i) => s + areas[i], 0);
    if (w >= h) {
      // Column along the left edge, full remaining height.
      const colW = sum / h;
      let ty = y;
      row.forEach((i, k) => {
        const th = k === row.length - 1 ? y + h - ty : areas[i] / colW;
        tiles.push({ x, y: ty, w: colW, h: th, ...entries[i] });
        ty += th;
      });
      x += colW;
      w -= colW;
    } else {
      // Row along the top edge, full remaining width.
      const rowH = sum / w;
      let tx = x;
      row.forEach((i, k) => {
        const tw = k === row.length - 1 ? x + w - tx : areas[i] / rowH;
        tiles.push({ x: tx, y, w: tw, h: rowH, ...entries[i] });
        tx += tw;
      });
      y += rowH;
      h -= rowH;
    }
  };

  let row: number[] = [];
  let i = 0;
  while (i < areas.length) {
    const side = Math.min(w, h);
    const rowAreas = row.map((r) => areas[r]);
    if (row.length === 0 || worst([...rowAreas, areas[i]], side) <= worst(rowAreas, side)) {
      row.push(i);
      i++;
    } else {
      layoutRow(row);
      row = [];
    }
  }
  if (row.length) layoutRow(row);
  return tiles;
}
