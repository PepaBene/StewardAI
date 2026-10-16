/** The in-page anchor id of a KPI's row on its holding page, and the full href to it —
 *  shared by the holding page (which sets the id) and the command palette (which links to
 *  it), so the two never drift apart. */
export function kpiAnchorId(kpiId: string): string {
  // Distinct from the KPI row's own `kpi-<id>` detail-panel id (only in the DOM when
  // expanded): this one is always present, so it's a stable link target.
  return `kpi-row-${kpiId.replace(/[^A-Za-z0-9_-]/g, "_")}`;
}

export function kpiHref(ticker: string, kpiId: string): string {
  return `/companies/${encodeURIComponent(ticker)}#${kpiAnchorId(kpiId)}`;
}
