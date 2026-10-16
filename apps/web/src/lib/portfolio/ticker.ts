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

// Stooq index symbols as people name them; the strip and benchmark settings use these symbols.
const INDEX_NAMES: Record<string, string> = {
  "^spx": "S&P 500", "^ndq": "Nasdaq Composite", "^ndx": "Nasdaq 100", "^dji": "Dow Jones", "^stoxx": "STOXX Europe 600",
  "^dax": "DAX", "^ukx": "FTSE 100", "^cac": "CAC 40", "^aex": "AEX", "^nkx": "Nikkei 225", "^px": "PX",
};

/** "^spx" -> "S&P 500"; a symbol it does not know is shown as given. */
export function indexName(symbol: string): string {
  return INDEX_NAMES[symbol.toLowerCase()] ?? symbol;
}
