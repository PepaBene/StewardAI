import type { components } from "@/lib/api/schema";
import { formatDate, formatPercent } from "@/lib/format";

type S = components["schemas"];
export type StressReplay = S["StressReplay"];
export type ReplayEpisode = S["ReplayEpisode"];
export type ReplayPosition = S["ReplayPosition"];

/** "12 Sep 2008 to 9 Mar 2009". */
export function episodeSpan(ep: Pick<ReplayEpisode, "start" | "end">): string {
  return `${formatDate(ep.start)} to ${formatDate(ep.end)}`;
}

/** How much of the book each kind of evidence covers, leaving out the parts that are zero. */
export function basisSentence(
  ep: Pick<ReplayEpisode, "real_weight" | "proxied_weight" | "unavailable_weight">,
): string {
  const pct = (x: number) => formatPercent(x, { decimals: 0 });
  const parts: string[] = [];
  if (ep.real_weight > 0.0005) parts.push(`${pct(ep.real_weight)} moved on its own prices`);
  if (ep.proxied_weight > 0.0005) parts.push(`${pct(ep.proxied_weight)} estimated from a similar ETF`);
  if (ep.unavailable_weight > 0.0005) parts.push(`${pct(ep.unavailable_weight)} not covered, held flat`);
  return parts.length ? `${parts.join(", ")}.` : "Nothing could be replayed.";
}

/** Width of a loss bar as a percentage of the track: gains and gaps give 0. */
export function lossWidth(loss: number | null | undefined, max: number): number {
  if (loss === null || loss === undefined || !Number.isFinite(loss) || !(max > 0)) return 0;
  return Math.min(100, Math.max(0, (-loss / max) * 100));
}

/** The label for how a position's move was found. */
export function basisLabel(basis: ReplayPosition["basis"]): string {
  if (basis === "real") return "own prices";
  if (basis === "proxied") return "estimated";
  return "not covered";
}
