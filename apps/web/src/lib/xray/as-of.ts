/** The `as_of` date the X-Ray tabs share through the address, so a chosen date follows you between
 * tabs and survives a reload. Anything that is not a real calendar date is ignored. */

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function parseAsOf(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  const m = DATE.exec(value);
  if (!m) return undefined;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  const real = d.getUTCFullYear() === Number(m[1]) && d.getUTCMonth() === Number(m[2]) - 1 && d.getUTCDate() === Number(m[3]);
  return real ? value : undefined;
}

/** `href` with the date carried over, or unchanged when there is none. */
export function withAsOf(href: string, asOf: string | undefined): string {
  return asOf ? `${href}?as_of=${asOf}` : href;
}

/** The query for an X-Ray request: only the date, only when one is chosen. */
export function asOfQuery(asOf: string | undefined): { as_of?: string } {
  return asOf ? { as_of: asOf } : {};
}
