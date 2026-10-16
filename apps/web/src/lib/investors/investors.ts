/* Pure helpers for the 13F pages: concentration tier, signed change, and overlap with your holdings. */

const num = (v: number | string | null | undefined): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export type Concentration = "concentrated" | "focused" | "broad";

/** By the number of reported positions: a short list is a concentrated book. */
export function concentrationTier(positions: number): Concentration {
  if (positions <= 25) return "concentrated";
  if (positions <= 100) return "focused";
  return "broad";
}

export const CONCENTRATION_LABEL: Record<Concentration, string> = {
  concentrated: "Concentrated (25 positions or fewer)",
  focused: "Focused (26 to 100 positions)",
  broad: "Broad (over 100 positions)",
};

/** Reported value against the previous quarter as a fraction; null when either is missing or the earlier value is zero. */
export function valueChange(current: number | string | null | undefined, previous: number | string | null | undefined): number | null {
  const c = num(current);
  const p = num(previous);
  if (c === null || p === null || p === 0) return null;
  return (c - p) / p;
}

export function signedPercent(fraction: number | null): string {
  if (fraction === null) return "No prior quarter";
  const pct = fraction * 100;
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

const SUFFIX = new Set(["inc", "incorporated", "corp", "corporation", "co", "company", "plc", "ltd", "limited", "holdings", "holding", "group", "sa", "nv", "ag", "the", "class", "cl", "com", "new"]);

/** "Apple Inc." and "APPLE INC" both become "apple"; used to match a 13F issuer to a holding by name. */
export function issuerKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .split(/\s+/)
    .filter((w) => w && !SUFFIX.has(w))
    .join(" ");
}

export type PositionLike = { issuer_name: string; value_usd: number | string | null };

/** The manager's positions that match one of your holdings by name, with their share of the reported value. */
export function overlapWithHoldings<T extends PositionLike>(positions: readonly T[], holdingNames: ReadonlyMap<string, string>, total: number | null) {
  const held = new Map<string, string>();
  for (const [ticker, name] of holdingNames) {
    const key = issuerKey(name);
    if (key) held.set(key, ticker);
  }
  const rows = positions.flatMap((p) => {
    const ticker = held.get(issuerKey(p.issuer_name));
    return ticker ? [{ position: p, ticker, share: total && total > 0 && num(p.value_usd) !== null ? num(p.value_usd)! / total : null }] : [];
  });
  return rows;
}

export type PositionChange = "new" | "added" | "trimmed" | "unchanged" | "sold" | "unknown";
export type MovableLike = PositionLike & { cusip: string; change?: PositionChange; share_change_pct?: number | null };

/** A position's quarter-on-quarter move in words, e.g. "+12%", "New", "Unchanged". */
export function changeWords(change: PositionChange | undefined, pct: number | null | undefined): string {
  if (change === "new") return "New";
  if (change === "sold") return "Sold out";
  if (change === "unchanged") return "Unchanged";
  if ((change === "added" || change === "trimmed") && pct !== null && pct !== undefined) return signedPercent(pct).replace(".0%", "%");
  return "—";
}

/**
 * The quarter's moves among the listed positions: the eight largest share changes as bars (in
 * percent), the new positions, and a one-line count.
 */
export function positionMoves<T extends MovableLike>(positions: readonly T[]) {
  const counts = { new: 0, added: 0, trimmed: 0, unchanged: 0 };
  for (const p of positions) if (p.change && p.change in counts) counts[p.change as keyof typeof counts] += 1;
  const bars = positions
    .filter((p) => (p.change === "added" || p.change === "trimmed") && p.share_change_pct !== null && p.share_change_pct !== undefined)
    .sort((a, b) => Math.abs(b.share_change_pct!) - Math.abs(a.share_change_pct!))
    .slice(0, 8)
    .map((p) => ({ label: p.issuer_name, value: p.share_change_pct! * 100 }));
  const opened = positions.filter((p) => p.change === "new");
  const parts = [
    counts.added ? `added to ${counts.added}` : null,
    counts.trimmed ? `trimmed ${counts.trimmed}` : null,
    counts.new ? `opened ${counts.new}` : null,
    counts.unchanged ? `left ${counts.unchanged} unchanged` : null,
  ].filter(Boolean);
  const sentence = parts.length ? `Among the largest ${positions.length} positions the manager ${parts.join(", ")}.` : "No share changes are known for these positions.";
  return { bars, opened, sentence };
}

const MANAGER_NOISE = /,?\s*\b(L\.?P\.?|LLC|Inc\.?|Corp\.?|Ltd\.?|Co\.?|Management|Advisors|Associates|Technologies)\b\.?/gi;

/** "Pershing Square Capital Management, L.P." -> "Pershing Square Capital"; for chips. */
export function shortManager(name: string): string {
  return name.replace(MANAGER_NOISE, "").replace(/\s+/g, " ").trim() || name;
}

/**
 * A tracked manager's move in one holding, in a few words. Share counts that grew more than
 * tenfold started from a token position, so the exact multiple is not shown.
 */
export function moveWords(change: PositionChange | undefined, pct: number | null | undefined): string {
  if (change === "new") return "new position";
  if (change === "sold") return "sold out";
  if (change === "unchanged") return "unchanged";
  if ((change === "added" || change === "trimmed") && pct !== null && pct !== undefined) {
    if (pct >= 9) return "added more than 10×";
    return `${change} ${Math.abs(Math.round(pct * 100))}%`;
  }
  return "no earlier quarter";
}

export function moveTone(change: PositionChange | undefined): "gain" | "loss" | "muted" {
  if (change === "new" || change === "added") return "gain";
  if (change === "sold" || change === "trimmed") return "loss";
  return "muted";
}

const MOVE_ORDER: Record<PositionChange, number> = { new: 0, sold: 1, added: 2, trimmed: 2, unchanged: 3, unknown: 4 };

/** New and sold-out first, then the largest share changes, then the unchanged. */
export function orderMoves<T extends { change: PositionChange; share_change_pct?: number | null }>(moves: readonly T[]): T[] {
  return [...moves].sort((a, b) => MOVE_ORDER[a.change] - MOVE_ORDER[b.change] || Math.abs(b.share_change_pct ?? 0) - Math.abs(a.share_change_pct ?? 0));
}

export type HolderCounts = {
  holders: number;
  previous_holders?: number | null;
  new_holders?: number | null;
  exited_holders?: number | null;
};

/** Filers that opened a position minus those that closed one; null without an earlier quarter. */
export function netHolders(h: HolderCounts): number | null {
  if (h.new_holders == null || h.exited_holders == null) return null;
  return h.new_holders - h.exited_holders;
}

/** One sentence over every covered holding: in how many did more filers buy in than sell out. */
export function breadthSentence(holdings: readonly HolderCounts[]): string | null {
  const nets = holdings.map(netHolders).filter((n): n is number => n !== null);
  if (nets.length === 0) return null;
  const more = nets.filter((n) => n > 0).length;
  const fewer = nets.filter((n) => n < 0).length;
  return `More funds opened a position than closed one in ${more} of your ${nets.length} US-listed holdings; more closed than opened in ${fewer}.`;
}

export { num as toNumber };
