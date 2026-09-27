/**
 * A portfolio treemap's tile colour (§5.5): gain/loss shading by magnitude of the move,
 * using the existing two-tone gain/loss tokens rather than inventing a new sequential ramp
 * — opacity stands in for magnitude, clamped at `cap` (a ±20% move is already the deepest
 * shade by default).
 */
export function treemapShadeOpacity(pplPct: number | null | undefined, cap = 0.2): number {
  if (pplPct === null || pplPct === undefined || !Number.isFinite(pplPct)) return 0.3;
  const magnitude = Math.min(1, Math.abs(pplPct) / cap);
  return 0.3 + magnitude * 0.7;
}
