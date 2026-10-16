const NAMED: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  ndash: "–",
  mdash: "—",
  lsquo: "‘",
  rsquo: "’",
  ldquo: "“",
  rdquo: "”",
  hellip: "…",
  euro: "€",
  pound: "£",
};

function fromCodePoint(n: number, whole: string): string {
  return Number.isInteger(n) && n > 0 && n <= 0x10ffff && !(n >= 0xd800 && n <= 0xdfff)
    ? String.fromCodePoint(n)
    : whole;
}

/**
 * Decode the HTML entities news feeds leave in headlines ("Ben &amp; Jerry&#39;s" ->
 * "Ben & Jerry's"). Named entities not in the short list above are left as they came.
 * Decoded once, so "&amp;lt;" becomes "&lt;", not "<".
 */
export function decodeEntities(text: string): string {
  return text.replace(/&(?:#(\d+)|#x([0-9a-f]+)|([a-z][a-z0-9]*));/gi, (whole, dec, hex, name) => {
    if (dec !== undefined) return fromCodePoint(Number(dec), whole);
    if (hex !== undefined) return fromCodePoint(parseInt(hex, 16), whole);
    return NAMED[String(name).toLowerCase()] ?? whole;
  });
}
