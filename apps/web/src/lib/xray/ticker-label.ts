/** "Apple Inc (AAPL_US_EQ)", or just the ticker when the name is unknown. */
export function formatTickerLabel(ticker: string, name: string | null | undefined): string {
  return name && name.trim() !== "" ? `${name} (${ticker})` : ticker;
}
