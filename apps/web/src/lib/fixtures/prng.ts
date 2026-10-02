/*
 * Deterministic pseudo-random numbers for the illustrative fixtures, so server and client
 * render identical charts and tests stay stable. Not for anything statistical.
 */

/** mulberry32: a small, well-distributed 32-bit generator. Returns numbers in [0, 1). */
export function seeded(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** A standard-normal draw (Box–Muller) from a uniform generator. */
export function gaussian(rand: () => number): number {
  const u = Math.max(rand(), 1e-12);
  const v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

/** Last day of month `m` (1-12) of `year` as "YYYY-MM-DD". */
export function monthEnd(year: number, m: number): string {
  const d = new Date(Date.UTC(year, m, 0));
  return d.toISOString().slice(0, 10);
}
