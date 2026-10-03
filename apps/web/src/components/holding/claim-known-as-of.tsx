import { claimKnownAsOf } from "@/lib/holding-data/logic";

type SourceLike = { as_of?: string | null; known_at?: string | null };

/** Date part of an ISO timestamp, or the text as is when it is not one. */
function day(iso: string): string {
  return /^\d{4}-\d{2}-\d{2}/.test(iso) ? iso.slice(0, 10) : iso;
}

/** "2026-03-01 to 2026-03-04", or one date when both bounds fall on the same day. */
export function knownAtRange(min: string, max: string): string {
  const a = day(min);
  const b = day(max);
  return a === b ? a : `${a} to ${b}`;
}

/**
 * Two separate facts per report claim, never mixed:
 *  - "Known to the app": when the app stored the cited records (`known_at_min`..`known_at_max` from the API).
 *    When a bound is missing it says so and counts the cited sources with no stored time; nothing is inferred.
 *  - "Source data dated": the period or snapshot dates (`as_of`) of the cited sources: what the data is about.
 * A sentence with no citation shows nothing.
 */
export function ClaimKnownAsOf({
  ids,
  sources,
  knownAtMin,
  knownAtMax,
}: {
  ids: readonly string[] | null | undefined;
  sources: ReadonlyMap<string, { source: SourceLike }>;
  knownAtMin?: string | null;
  knownAtMax?: string | null;
}) {
  const lookup = new Map<string, SourceLike>();
  for (const [id, entry] of sources) lookup.set(id, entry.source);
  const k = claimKnownAsOf(ids ?? [], lookup);
  if (k.cited === 0) return null;
  const dated = k.date ? `Source data dated ${k.oldest && k.oldest !== k.date ? `${k.oldest} to ${k.date}` : k.date}` : "Cited sources undated";
  const undated = k.undated > 0 && k.date ? ` (+${k.undated} undated)` : "";
  const hasBounds = Boolean(knownAtMin) && Boolean(knownAtMax);
  let noKnownAt = 0;
  if (!hasBounds) {
    for (const id of new Set(ids ?? [])) {
      const s = lookup.get(id);
      if (s && !s.known_at) noKnownAt += 1;
    }
  }
  const known = hasBounds
    ? `Known to the app: ${knownAtRange(knownAtMin as string, knownAtMax as string)}`
    : noKnownAt > 0
      ? `Known-at not recorded for ${noKnownAt} cited source${noKnownAt === 1 ? "" : "s"}`
      : "Known-at not recorded for this claim";
  return (
    <span
      className="num ml-1 font-sans text-[11px] text-text-muted"
      title="Known to the app is when the app stored the cited records (null on older reports and for sources with no stored time, never inferred). Source data dated is the period end, snapshot or publication date the data is about, not when the app learned of it."
    >
      · <span className="whitespace-nowrap">{known}</span> · <span className="whitespace-nowrap">{dated}{undated}</span>
    </span>
  );
}
