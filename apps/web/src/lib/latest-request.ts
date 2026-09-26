/** The outcome of a request, unless a newer one started meanwhile (then it is `stale`). */
export type LatestOutcome<T> = { stale: true } | { stale: false; ok: true; value: T } | { stale: false; ok: false; error: string };

/**
 * Run `request` for the current generation of `sequence` (a ref the list bumps on every fresh
 * load, e.g. a filter change). If the list was reloaded while this was in flight, the answer
 * belongs to the old filters and comes back `stale`, so a "load more" never mixes pages.
 */
export async function latestOnly<T>(sequence: { current: number }, request: () => Promise<T>): Promise<LatestOutcome<T>> {
  const mine = sequence.current;
  try {
    const value = await request();
    return mine === sequence.current ? { stale: false, ok: true, value } : { stale: true };
  } catch (err) {
    if (mine !== sequence.current) return { stale: true };
    return { stale: false, ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}
