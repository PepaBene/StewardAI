/** Display names of the providers the backend ledgers know; any other provider is title-cased from its id. */
export const PROVIDER_LABELS: Readonly<Record<string, string>> = {
  alpha_vantage: "Alpha Vantage",
  eodhd: "EODHD",
  world_bank: "World Bank",
  oecd: "OECD",
  fmp: "FMP",
  diffbot: "Diffbot",
  people_data_labs: "People Data Labs",
  epo_ops: "EPO OPS",
  finnhub: "Finnhub",
  twelve_data: "Twelve Data",
  bis: "BIS",
  ecb: "ECB",
  un_comtrade: "UN Comtrade",
  fred: "FRED",
  eurostat: "Eurostat",
  cnb: "ČNB",
  ted: "TED",
  rss: "RSS",
  macro_release_calendar: "Macro release calendar",
};

export function providerLabel(provider: string): string {
  if (Object.hasOwn(PROVIDER_LABELS, provider)) return PROVIDER_LABELS[provider];
  const words = provider.split("_").filter(Boolean);
  return words.length === 0 ? provider : words.map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}
