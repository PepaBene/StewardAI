/**
 * A Trading212 ticker without its venue suffix, for tight chart labels: "NVDA_US_EQ" ->
 * "NVDA", "VWCEd_EQ" -> "VWCE", "NOVOb_EQ" -> "NOVO B" (share class kept), "CEZ_EQ" -> "CEZ".
 * Tables and links keep the full ticker.
 */
export function shortTicker(ticker: string): string {
  const m = /^([A-Z0-9.]+?)([a-z])?_(?:[A-Z]{2}_)?EQ$/.exec(ticker);
  if (!m) return ticker;
  const [, root, suffix] = m;
  // Lower-case letters are Trading212's exchange codes, except b/a share classes on Nordic listings.
  return suffix === "b" ? `${root} B` : root;
}
