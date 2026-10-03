import { claimKnownAsOf } from "@/lib/holding-data/logic";

/**
 * "Known as of" for one report claim. The report payload carries no per-claim known-at date,
 * so this is derived, and says so: the latest `as_of` among the sentence's cited sources.
 * Undated citations are counted, never ignored; a sentence with no citation shows nothing.
 */
export function ClaimKnownAsOf({ ids, sources }: { ids: readonly string[] | null | undefined; sources: ReadonlyMap<string, { source: { as_of?: string | null } }> }) {
  const lookup = new Map<string, { as_of?: string | null }>();
  for (const [id, entry] of sources) lookup.set(id, entry.source);
  const k = claimKnownAsOf(ids ?? [], lookup);
  if (k.cited === 0) return null;
  const text = k.date ? `Cited sources as of ${k.date}` : "Cited sources undated";
  const note = k.undated > 0 && k.date ? ` (+${k.undated} undated)` : "";
  return (
    <span
      className="num ml-1 font-sans text-[11px] whitespace-nowrap text-text-muted"
      title="The report stores no known-at date per claim. This is the latest as-of date of the sources the sentence cites."
    >
      · {text}
      {note}
    </span>
  );
}
