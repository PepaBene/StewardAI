export type GlossaryTerm = {
  slug: string;
  term: string;
  category: string;
  definition: string;
  formula?: string;
  example?: string;
  related?: string[];
};

export const GLOSSARY: GlossaryTerm[] = [
  { slug: "annualized-return", term: "Annualized return", category: "Performance", definition: "The compounded yearly rate of return over a period longer or shorter than one year.", formula: "(Ending value / Starting value)^(1 / years) − 1", related: ["cagr", "total-return"] },
  { slug: "asset-allocation", term: "Asset allocation", category: "Portfolio", definition: "How a portfolio is divided among asset classes, regions, sectors, or currencies." },
  { slug: "bear-market", term: "Bear market", category: "Markets", definition: "A prolonged period of falling prices, commonly described as a decline of at least 20% from a recent high." },
  { slug: "benchmark", term: "Benchmark", category: "Performance", definition: "A reference index or portfolio used to put investment performance into context." },
  { slug: "beta", term: "Beta", category: "Risk", definition: "A measure of how much an investment has moved relative to the market. A beta above 1 indicates larger historical moves than the benchmark; below 1 indicates smaller moves." },
  { slug: "bull-market", term: "Bull market", category: "Markets", definition: "A sustained period of rising market prices and improving investor expectations." },
  { slug: "cagr", term: "CAGR", category: "Performance", definition: "Compound annual growth rate: the smoothed yearly growth rate that connects a starting value to an ending value.", formula: "(Ending value / Starting value)^(1 / years) − 1", related: ["annualized-return"] },
  { slug: "capital-gains", term: "Capital gains", category: "Returns", definition: "The profit made when an investment is sold for more than its purchase cost. A loss occurs when it is sold for less." },
  { slug: "cost-basis", term: "Cost basis", category: "Portfolio", definition: "The amount paid to acquire an investment, adjusted for relevant fees and corporate actions. It is used to calculate profit or loss." },
  { slug: "diversification-ratio", term: "Diversification ratio", category: "Risk", definition: "The volatility of holding each position on its own, weighted by size, divided by the volatility of the whole portfolio. 1.00 means mixing the positions does not reduce risk; higher means the positions offset each other.", formula: "Σ (weight × volatility of each position) / Portfolio volatility", related: ["volatility", "effective-bets"] },
  { slug: "dividend", term: "Dividend", category: "Returns", definition: "A distribution of company profits or reserves to shareholders, usually paid in cash per share." },
  { slug: "dividend-yield", term: "Dividend yield", category: "Valuation", definition: "Annual dividends per share expressed as a percentage of the current share price.", formula: "Annual dividend per share / Share price × 100" },
  { slug: "drawdown", term: "Drawdown", category: "Risk", definition: "The percentage decline from a portfolio or investment's previous peak to a later low.", formula: "(Trough value − Peak value) / Peak value × 100" },
  { slug: "earnings-per-share", term: "Earnings per share (EPS)", category: "Fundamentals", definition: "A company's profit attributable to each common share, typically reported on a diluted basis.", formula: "Net income attributable to common shareholders / Weighted average diluted shares" },
  { slug: "effective-bets", term: "Independent bets", category: "Risk", definition: "How many separate sources of risk a portfolio really holds. Ten names that move together can be one or two bets. It is the effective number of bets of Meucci, computed from how the portfolio's risk splits between independent patterns of movement.", related: ["diversification-ratio", "risk-share"] },
  { slug: "enterprise-value", term: "Enterprise value (EV)", category: "Valuation", definition: "A measure of a company's total operating value, accounting for debt and cash as well as equity value.", formula: "Market capitalization + Debt + Preferred equity + Minority interest − Cash" },
  { slug: "factor-loading", term: "Factor loading", category: "Risk", definition: "How much a holding's weekly return moves with a factor such as the stock market, company size, value versus growth, profitability, investment, momentum, a currency or the 10-year yield. A loading of 1.0 on the market means it moves one for one with it. Loadings with a t-statistic under 2 are within noise.", related: ["beta", "factor-tilt"] },
  { slug: "factor-tilt", term: "Factor tilt", category: "Risk", definition: "The portfolio's loading on a factor minus the benchmark's. A tilt beyond 0.3 on a style factor is shown as a concentration note: the portfolio leans on that bet more than the benchmark does. It is a prompt to look, not a limit.", related: ["factor-loading"] },
  { slug: "free-cash-flow", term: "Free cash flow (FCF)", category: "Fundamentals", definition: "Cash generated after a company pays for the capital spending needed to maintain or grow its business.", formula: "Cash from operations − Capital expenditures" },
  { slug: "gross-margin", term: "Gross margin", category: "Fundamentals", definition: "The share of revenue remaining after direct costs of goods or services are paid.", formula: "(Revenue − Cost of revenue) / Revenue × 100" },
  { slug: "market-capitalization", term: "Market capitalization", category: "Valuation", definition: "The market value of a company's outstanding equity.", formula: "Share price × Shares outstanding" },
  { slug: "net-margin", term: "Net margin", category: "Fundamentals", definition: "The share of revenue a company keeps as net income after all expenses, interest, and taxes.", formula: "Net income / Revenue × 100" },
  { slug: "operating-margin", term: "Operating margin", category: "Fundamentals", definition: "The share of revenue left after operating expenses, before interest and income taxes.", formula: "Operating income / Revenue × 100" },
  { slug: "payout-ratio", term: "Payout ratio", category: "Valuation", definition: "The proportion of earnings paid to shareholders as dividends.", formula: "Dividends per share / Earnings per share × 100" },
  { slug: "price-to-book", term: "Price-to-book (P/B)", category: "Valuation", definition: "The share price compared with the company's book value per share.", formula: "Share price / Book value per share" },
  { slug: "price-to-earnings", term: "Price-to-earnings (P/E)", category: "Valuation", definition: "The share price relative to earnings per share. Trailing P/E uses past earnings; forward P/E uses estimates.", formula: "Share price / Earnings per share", related: ["earnings-per-share"] },
  { slug: "price-to-sales", term: "Price-to-sales (P/S)", category: "Valuation", definition: "The market value of equity relative to annual revenue.", formula: "Market capitalization / Revenue" },
  { slug: "return-on-equity", term: "Return on equity (ROE)", category: "Fundamentals", definition: "How much net income a company generates relative to shareholders' equity.", formula: "Net income / Average shareholders' equity × 100" },
  { slug: "return-on-invested-capital", term: "Return on invested capital (ROIC)", category: "Fundamentals", definition: "How efficiently a company turns the capital invested in its operations into after-tax operating profit.", formula: "NOPAT / Average invested capital × 100" },
  { slug: "risk-share", term: "Share of risk", category: "Risk", definition: "The part of the portfolio's forecast volatility a position accounts for, counting how it moves with everything else. Shares add up to 100 %. A 6 % position can carry 15 % of the risk. Also called its contribution to risk.", formula: "weight × (covariance matrix × weights) / portfolio variance", related: ["volatility", "effective-bets"] },
  { slug: "risk-tolerance", term: "Risk tolerance", category: "Risk", definition: "An investor's ability and willingness to withstand uncertainty and losses while pursuing their goals." },
  { slug: "sharpe-ratio", term: "Sharpe ratio", category: "Risk", definition: "The average return above a risk-free rate per unit of return volatility. Higher values indicate more return for the measured risk.", formula: "(Portfolio return − Risk-free rate) / Standard deviation of returns" },
  { slug: "total-return", term: "Total return", category: "Returns", definition: "The full gain or loss over a period, including price changes and distributions such as dividends, usually assuming reinvestment." },
  { slug: "volatility", term: "Volatility", category: "Risk", definition: "The degree to which prices or returns fluctuate over time, often measured as the standard deviation of returns." },
  { slug: "yield", term: "Yield", category: "Returns", definition: "Income generated by an investment over a period, expressed as a percentage of its price or value. The exact calculation depends on the instrument." },
];

export function findGlossaryTerm(slug: string) {
  return GLOSSARY.find((term) => term.slug === slug);
}
