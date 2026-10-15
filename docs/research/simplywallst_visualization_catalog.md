# Simply Wall St: what it shows and how it displays it

Reviewed 2026-10-15 with Playwright (screenshots taken at about 1030 px wide, dark theme). Companion file:
`simplywallst_feature_and_analysis_review.md` (what it analyses and what StewardAI could add).

Scope note: chart types below were read from screenshots and page text. Where I did not see an interaction (for example
dragging the Snowflake filter) it is marked unverified. Colours are described by role and approximate hue; exact values were
not extracted.

## 1. The visual system in one page

- **Theme.** Dark charcoal page, slightly lighter cards with rounded corners and no hard borders, serif display headings
  (panel titles, page titles) over a sans body. Light text with three grey levels (primary, soft, faint).
- **Colour carries meaning, consistently across every chart:**
  green = cheap, good, passed, gain; red = expensive, bad, failed, loss; amber/gold = brand colour, primary buttons, selected
  tab, neutral snowflake; purple = forecast, estimate, analyst; blue = revenue; teal/mint = earnings; magenta/pink = fair value
  line; a blue-to-purple-to-red gradient = risk. Past is solid, future or estimated is a different hue or dashed, never just
  lighter.
- **Zones instead of gridlines.** Several charts paint the plot background itself green on the undervalued side and red on
  the overvalued side, with a vertical marker at the neutral point (0%, fair value, peer average). The reader sees good and
  bad before reading an axis.
- **Every number gets a sentence.** Pass/fail rows are an icon plus a bold lead-in plus a sentence with the figures
  ("Below Future Cash Flow Value: MSFT ($533.69) is trading above our estimate ($494.64)"). Cards give a one-line
  interpretation under each metric ("vs market 12.9%, more volatile").
- **Chart furniture is standard.** Under almost every chart: a `Data` button (shows the underlying table), a `Learn` button
  (video or guide), and a `...` menu. Titles carry an `i` tooltip. A footer table lists each dataset, last-updated time,
  timeframe and the public source form.
- **Large headline numbers, small labels.** KPI rows use a big figure with a small caption under it and a coloured left
  rule or arrow for sign. Green and red values are always accompanied by a sign or arrow.
- **Honest empty and limited states.** "LIMITED DATA" badges on partial metrics, "n/a" in cells, an explicit message when a
  check does not apply ("not paying a notable dividend for the US market, therefore no need to check").
- **Navigation.** Top bar (Dashboard, Portfolios, Watchlist, Community, Discover, Screener, global search with Ctrl K,
  notifications, avatar), page tabs underneath, and on stock pages a sticky left sidebar with the Snowflake, the company name,
  an Add-to-portfolio button and numbered sections 1 to 7. Charts and sections lazy-load as you scroll.

## 2. The Snowflake (the signature visual)

- **Form.** A five-axis polar chart. Three concentric rings give the 0-6 scale background; axis names (Value, Future, Past,
  Health, Dividend) are set along the outside of the ring on a curve; the score is drawn as a single filled organic blob with a
  soft edge, not a straight-edged radar polygon. A larger blob means a stronger company.
- **Colour.** The blob's colour is the overall sentiment (yellow for balanced, green for strong, orange or red for weak).
  It is the one place colour is by company, not by sign.
- **Sizes it appears at.** Tiny avatar in table rows (holdings, screener, dividend quality tables); medium on stock-report
  sidebar and holdings drawer; large with axis labels on the stock overview, the portfolio Analysis page and the screener's
  filter; card-sized on screener grid cards and Discover idea cards where it is the main image.
- **As an aggregate.** The same shape is reused for a whole portfolio ("Portfolio Snowflake" with a verdict such as "A past
  performer with good growth potential") and for a whole investing idea (the average of its stocks).
- **As a filter.** The screener shows the Snowflake with five draggable nodes to set minimum scores (interaction unverified;
  plain clicks did nothing).
- **Why it works.** One recognisable mark that scales from 24 px to 300 px and means the same thing everywhere, so a table
  of 150 stocks can be scanned by shape.

## 3. Portfolio pages

### Holdings
- **KPI strip:** total value with holding count, 1-day return (amount and percent), total return (amount and percent),
  annualised IRR; with a `LIMITED DATA` badge where cost data is missing.
- **Value over time:** area/line chart with a second line for cost basis, range chips (1M, 3M, YTD, 1Y, 2Y, All time;
  7D to 3Y on a smaller portfolio), a Stacked/Combined switch, and a second tab "Performance vs market".
- **Return split cards:** four small cards (unrealised, realised, dividends, currency impact) each with amount and percent,
  plus a fifth for estimated dividends and yield. The currency card is red when negative.
- **Holdings table:** one row per holding with a Snowflake avatar, ticker in amber, company name in grey, price, a fair-value
  chip (price against fair value as "26.6% undervalued", green or red), 1-day return, total return, value over cost. A view
  selector switches columns between Check-In, Holding, Income, Growth, Value, Snowflake and Fundamentals, plus a custom view.
  The Fundamentals preset shows net margin, past and future earnings growth, debt to equity, ROCE, ROE.
- **Check-In view:** a richer row: return and value, an event chip with an icon and date (News, Event, Valuation, New Risk),
  price against fair value, narrative title, and a 1-year sparkline in which the price is a green or red area and the fair
  value is a stepped magenta line, with a vertical marker at today.
- **Side drawer:** clicking a row opens a right panel with tabs (Overview, Holding, Updates, Notes), important updates, a
  notes field and a "Full Analysis" button, so the table stays visible.
- **Prompt cards:** inline cards ("Track the Narrative, not just the ticker") and a "What's your strategy?" editable line under
  the portfolio name.

### Analysis
- **Scorecard:** the polar chart with a clickable point per axis; next to it the selected axis shows `Future 2.67 / 6`, a
  one-line explanation, and two lists, "Lifting the score" and "Holding it back", each row a company logo, name, a thin
  horizontal progress bar coloured green or red, the score `n/6` and the holding's weight.
- **Ranked dot strips, one per axis** (valuation, forecast earnings growth, ROE, debt to equity, forecast yield): a horizontal
  axis in the metric's units, each holding a labelled circle placed by value (circle size varies with weight on the
  valuation strip), the market shown as a labelled reference line ("US Market 17.3%") and the portfolio as another ("Portfolio
  30.0%"). On the valuation strip the left half is tinted green and the right half red, with a dashed fair-value line at 0%.
  Tabs switch the basis (Fair Value, DCF, PE, PS, PB, Narrative Fair Value; Earnings, Revenue, EPS; ROE, ROCE, ROA).
  An expand icon opens it full size.
- **Diversification:** a left-to-right Sankey, Portfolio then Sector then Industry then Ticker, band width = weight, labels
  with percentages at each node and a Sector/Industry/Ticker toggle. Beside it a holdings concentration bar (largest holdings,
  "Others") and a horizontal bar chart of revenue by geography with a "View all" link.
- **Correlation:** two short ranked lists ("Moves together", "Moves least together", each pair with a coefficient) and a
  lower-triangle matrix of rounded cells shaded on a diverging scale from blue (move oppositely) through neutral grey at 0 to
  amber (move together), coefficient printed in each cell, a legend bar, and the hint "Hover a cell to compare two holdings".
  Only the lower triangle is drawn, so there is no redundant mirror and no diagonal of 1.00.
- **Risk:** a horizontal gradient bar from blue (low) through purple to red (very high) with two labelled markers ("My
  Portfolio", "S&P 500 (US)") and the score `78/100` at the top right; six metric cards (volatility, max drawdown, top-3
  concentration, beta, Sharpe, Sortino), each a label, a large value and a one-line verdict against a benchmark; a tab strip
  (Volatility, Max Drawdown, Beta) over a ranked per-holding bar chart with portfolio and market as labelled reference bars.

### Valuations
- **Header:** three KPIs with coloured left rules (portfolio value green, fair value magenta, undervalued percent in green),
  the subtitle "Last 12 months, undervalued at today's prices".
- **Main chart:** price as a red area (the colour reflects the portfolio's position against fair value; only the red state
  was seen) against a stepped magenta fair-value line, with a vertical amber cursor at today and a date label.
- **Summary row:** share of the portfolio undervalued and overvalued as percent plus holding count, stale narratives as
  "11 out of 15", weighted aggregate upside as a currency amount.
- **Quick-assign:** a row of three pill buttons (Most Popular, Analyst Consensus, DCF Analysis) that re-value every holding
  instantly; copy states nothing is saved.
- **Grouped tables:** collapsible sections with counts (Overvalued 7, Stale narratives 5, Undervalued and fairly valued 3),
  each with the explanatory line, then rows of ticker, 1-year return, price with "36.0% overvalued", fair value with the
  author's initial, narrative title, last published ("Updated 724 days ago"), last update chip.

### Dividends
- **Hero:** next-12-month income as the largest number on the page, a green arrow with "5.8% higher vs last 12m", then monthly
  income, current yield, yield on cost as three small stats.
- **Dividend history:** a monthly bar chart across three years, green bars, with a Monthly/Annual toggle. Hovering a bar
  fills a tooltip panel beside it (month, "1 paid", and a small table of symbol, pay date, amount).
- **Contributors:** a two-column list of horizontal bars with the ticker and name at left and the amount at right, one column
  for largest and one for smallest contributors, bars scaled to the largest.
- **Dividend quality:** left, three tier rows with coloured dots (Low 0-2 red, Medium 3-4 amber, High 5-6 green), each with
  holding count, income amount and percent; under them a single stacked horizontal bar showing income share by tier. Right,
  a table with a Snowflake avatar, 12-month payment and its share of income, yield against yield on cost, and a `5/6` score
  chip with dividend growth.
- **Estimated future payments:** the same monthly bar chart in purple, because it is a forecast, with a toggle to a 3-year
  annual view and the same hover panel (declared versus estimated payments listed separately).
- **Calendar:** a month grid (Sunday to Saturday), today highlighted, each event a small coloured dot with a label chip
  ("NVDA Pay: US$10.00", "BATS Ex-Div"); a legend of four dot styles (ex-dividend, estimated ex-dividend, payment, estimated
  payment); previous and next month arrows. Under it an events table (date, ticker, event, estimated total).
- **Footer link in each block:** "Discover top quality dividend stocks" turns the analysis into a screener.

### Returns
- **Returns breakdown:** a waterfall of unrealised, realised, dividends, currency and total, floating bars on a zero line,
  green for positive, a flat line for a tiny negative, with the amounts above each bar.
- **Contributors to returns:** two columns (highest, lowest); each row has ticker, name, a small bidirectional bar centred on
  zero, the amount and the percent.
- **Detailed report:** wide table per holding (shares, price, value, cost basis, unrealised, realised, dividends, currency
  gains, total gains, annualised IRR) with a totals row that also shows each component as a percent of cost, a
  "Liquidated holdings" divider, per-holding expandable "Returns summary" and a transaction list (BUY, SELL, SPLIT 20:1 with
  cash flow), and a Download as CSV button. A date-range selector (Since inception) sits at the top.

### Updates and dashboard feed
- Filter chips (All, Live News, Community, Earnings, Analyst, Valuation, Dividends, Insider Trades, Risks, Other) each with a
  small purple dot when unread; cards with a company logo, company name, event type and age, and on the right ticker, price
  and 1-day move with a dropdown; headline in the serif face, a two-line summary with "Show more", and a like button.
  Narrative-update cards add the author, target label, fair value and an intrinsic discount line with 1Y, 7D, 1D returns.
- Dashboard: a portfolio tile (logos stack with "+3", value, 1D and 3M return, sparkline), a "New portfolio" tile, the feed in
  the main column and community posts in a right rail.

### Watchlist
- Same row structure as holdings with a 7D and 3M return pair, a sparkline against a stepped fair-value line, and a narrative
  selector ("Select") for each name; a "3M" range control and a "Narrative View" switch above the table.

## 4. Stock report pages

- **Header:** company name, exchange and ticker, market cap, "Updated 1d ago, data from company financials and 51 analysts",
  share price, the chosen fair value with its source label (here "AnalystConsensusTarget"), and "6.6% undervalued" as a coloured
  chip, followed by 1Y, 7D and 1D returns. A "Portfolio value" strip shows what the user's portfolio does if they hold it.
- **Overview.** Snowflake plus Rewards (green star bullets) and Risk analysis (orange warning bullets), each bullet expandable;
  a "See All Risk Checks" button. Then the earnings-call card (headline summary, "Full Transcript", a text box "Ask anything"
  and suggested question chips), community fair values (a horizontal histogram strip of fair values set by users, with the
  last price marker and valuation range), narrative cards (author, fair value, percent discount, views, likes, comments),
  a competitors row of four cards each with a Snowflake, a price-history table, news, shareholder-returns table (company
  against industry against market for 7D and 1Y with a pass sentence), price volatility (average weekly movement against
  industry and market), an About block (founded, employees, CEO, website), a fundamentals summary, earnings and revenue block
  with margins, a dividend block, and the data-status footer.
- **Risk Checks dialog:** one row per check with a red cross or green tick, the question in bold, the answer as a sentence and a
  section reference. Failed checks are listed first.
- **Valuation (1):** (a) a horizontal three-band bar, green 20% undervalued, amber about right, red 20% overvalued, with
  rotated labels and a marker at the stock; (b) a price against DCF value history chart with a stepped magenta line;
  (c) a peers chart: horizontal bars of the multiple, company bar highlighted with a tag, a vertical "Peer Avg" line with
  the left side green and the right side red, and a right-hand column of estimated earnings growth, with a Forward PE
  toggle and an "Edit Peers" button; (d) an industry histogram: vertical bars of company counts per multiple bin, the
  company's bin in blue, a labelled industry-average line, green background left of the average and red right of it, a
  tab strip of bins below (PE 25-30 and so on), and a table of that bin's companies; (e) a multiple-history chart with a
  3M/1Y/3Y/5Y selector and a date table; (f) a "fair ratio" comparison; (g) the analyst target chart: share price line,
  consensus target line in purple, a shaded dispersion band, a golden future zone with a red-shaded cone and a dashed line to
  the 12-month target, a tooltip card showing date, analyst count, price, average target with percent and the "Agreement"
  rating text; and a table of month, price, target, dispersion, high, low and 1-year actual price.
- **Future (2):** a stacked headline (criteria score, growth sentence, key figures), update cards ("Analysis Article", "Price
  Target Changed" with a small label), then an area chart that joins reported history to analyst forecasts: a "Past" shaded
  strip, a vertical divider, revenue in blue and earnings in teal, series chips (Revenue, Earnings, Free Cash Flow, Cash From
  Op) that toggle lines, a hover card with the date and annual figures. A numbered subsection heading (2.2, 2.3) with a pass
  or fail sentence under each chart.
- **Past (3):** a Sankey with a year timeline strip on top (a mini line chart labelled 2015 to 2026, apparently a year
  selector; clicking it was not tested): three
  segment bars on the left (blue) into Revenue, Revenue into Gross profit and Cost of sales, Gross profit into Earnings and
  Expenses, Expenses into General and administrative, R&D, Sales and marketing, Non-operating; each node labelled with
  its name and amount; then a quarterly history table.
- **Health (4):** paired vertical bars, Short term and Long term, each pairing assets (blue) with liabilities (mint) with
  labelled amounts, so coverage is visible at a glance; a debt-to-equity history chart and checks with green ticks.
- **Dividend (5):** a stability and growth chart (annual dividend amount and yield through time, past and analyst forecast,
  with series chips for Yield, Payments, Annual Amount, EPS), a yield-versus-market bar showing company, bottom 25%, top 25%,
  industry average and forecast on one scale, and a countdown line ("Buy in the next 34 days to receive the upcoming dividend").
- **Management (6):** key facts as stat tiles (CEO, compensation, salary percent, tenure, ownership, average tenures),
  update cards, a CEO compensation analysis, and leadership and board tables.
- **Ownership (7):** insider transaction table with Buy/Sell tags, an insider trading volume chart with a one-line
  verdict, an ownership breakdown table by holder type, and a top-shareholders table (ownership percent, holder, shares,
  value, change, share of the holder's portfolio). I did not see a chart for the ownership split beyond tables.
- **Narrative page:** a header with fair value, share price and discount; a price chart with a dashed pink fair-value line to
  the right edge; a Follow Fair Value button with follower count; the thesis text; a right rail with revenue and earnings
  projected to 2031 (past shaded, forecast as curves, end labels "Revenue US$1.1t", "Earnings US$703.8b"), two growth and
  margin stats and a "Valuation details" button that opens the valuator modal (tabs Valuator, Company Info, Notes), a
  5-year projection chart with Revenue, Profit Margin and Future PE tabs, sliders with "Decrease / Increase" and the default,
  historical and industry values beside each input, and the printed calculation chain.

## 5. Screener and Discover

- **Top block:** idea chips ("Dividend payers yielding over 4%", rotating), a single-line plain-English box with a sparkle icon
  ("Ask Charlie to build a screen..."), and "Charlie understands metrics, snowflake analysis and any company characteristics".
- **Filter block:** the Snowflake filter on the left; three tall cards (Markets, Industries, Metrics) that list the chosen
  values as chips, a "Hide secondary listings" chip and a Keywords field. The result of the plain-English query filled them as
  chips (United States; Dividend Yield, Debt to Equity Ratio, Past 1 Year Earnings Growth) and showed "158 results, 4 new
  companies last 7d".
- **Advanced filters dialog:** a long white-on-dark list in groups (Company, Valuation, Future Growth Estimate, Past
  Performance, Financial Health, Dividends, Management, Other). Each metric is a checkbox with its name and a parenthetical
  definition; ticking reveals a dual-handle range slider with non-linear tick labels (market cap 0M, 10M, 100M, 1000M, 10000M,
  infinity; share price 0, 7, 45, 200, infinity; negative values mirrored, such as -100%, -50%). Toggles at the bottom for
  upcoming dividend, founders running the business, ETFs and funds.
- **Results:** a table with Snowflake avatar, ticker and name, last price, 7D and 1Y returns (green and red), market cap,
  analyst target, valuation as a labelled multiple (`PE 24.1x`, `PS`, `PB` chosen per company), growth as `E 25.9%`, dividend
  yield, a star to watch, sort control (default market cap, high to low), and a list/grid switch. The grid is a card per
  company: name, logo, a two-line description, the large Snowflake, ticker, market cap, 7D and 1Y, and a Watch button.
- **Paywall pattern:** an upgrade line replaces the list of new companies; free users see the top 4 of any screen; a
  sign-up modal appears after scrolling.
- **Discover:** a card per idea with an editorial illustration, the idea name, an aggregate Snowflake and an avatar stack
  ("+20 companies", "2 new"). An idea page opens with a hero (illustration behind the aggregate Snowflake), a short essay,
  market and industry selectors, and the same results table with Edit Screener and Get New Stock Alert buttons.

## 6. Mapping to StewardAI

StewardAI draws with a custom SVG kit in `apps/web/src/components/charts` (bar, candle, donut, drawdown, hbar, heat, line,
radar, revision-line, ring, sankey, sparkline, treemap, vintage-fan, waterfall) and uses recharts only for the price chart.
That kit covers most of what is below; the unknowns are marked because I did not open every component.

| Simply Wall St visual | Reuse in StewardAI | New work |
| --- | --- | --- |
| Five-axis Snowflake, mini to large | `charts/radar.tsx` (used in `risk-panel.tsx` and `holding-overview.tsx`) | An organic filled shape and a 24 px mode for table rows; fixed 0-6 scale |
| Portfolio Sankey (sector to industry to ticker) | `charts/sankey.tsx` | Data prep for portfolio weights |
| Income-statement Sankey with a year strip | `charts/sankey.tsx` | A year selector built from a small line chart |
| Waterfall of returns | `charts/waterfall.tsx`, recharts `contribution-waterfall` | Currency component if absent |
| Correlation half-matrix | `risk-view.tsx` already draws a matrix (a table with shaded cells); `charts/heat.tsx` | Diverging blue-grey-amber scale and lower triangle only |
| Holdings sparkline against a stepped fair-value line | `charts/sparkline.tsx`, `line-chart.tsx` | A second step series |
| Ranked dot strip with reference lines and tinted zones | none | New component: axis, labelled dots, optional size by weight, reference lines, green and red half-planes |
| Three-band valuation gauge and risk gradient bar with markers | `charts/ring.tsx` is circular only | New horizontal gauge component taking bands, markers and labels |
| Industry histogram with a highlighted bin and per-bin table | `charts/bar-chart.tsx` | Bin highlight, average line, tinted halves |
| Peer horizontal bars with a peer-average line and a growth column | `charts/hbar.tsx` | Reference line and side column |
| Analyst target chart with dispersion band and forecast cone | `charts/vintage-fan.tsx` (a fan chart) and `revision-line.tsx` | Band around a line, cone to a target, tooltip card |
| Monthly dividend bars with hover panel, history green and forecast purple | `charts/bar-chart.tsx` | Per-bar tooltip table, forecast styling |
| Dividend calendar | none | New month-grid component with typed dots and legend |
| Quality tier stacked bar and tier rows | `charts/bar-chart.tsx` or `hbar.tsx` | A stacked single bar |
| Paired short-term and long-term assets and liabilities bars | `charts/bar-chart.tsx` | Grouped bars |
| Contributors bars centred on zero | `charts/hbar.tsx` | Zero-centred mode |
| Area chart joining history and forecast, with series chips | `charts/line-chart.tsx` | Shaded past strip, chip toggles |
| Treemap or concentration bar | `charts/treemap.tsx`, `charts/donut.tsx` | none |
| Table view presets, side drawer | `components/ui`, holdings table | Preset definitions, drawer |
| Data button, Learn button, provenance footer | evidence components (`confidence-badge`, `provenance-tag`, `known-at`) | One shared chart-footer component |

## 7. Patterns worth copying, in priority order

1. **One recognisable mark, used at every scale** (the Snowflake). StewardAI already has a radar; making it a compact,
   consistent score mark that appears in holdings and screener rows would cut scanning time more than any new chart.
2. **Sentences next to numbers.** Pass/fail rows with an icon, a bold lead-in and the figures. This also makes the AI
   narrative layer and the deterministic layer line up visibly.
3. **Tinted zones and reference lines** (green below fair value, red above, a marked peer average, a marked portfolio), so
   a chart reads before its axis does.
4. **A `Data` toggle and a provenance footer on every chart.** StewardAI already stores `known_at` and evidence ids, so this
   is cheap and fits its trust-first positioning.
5. **A consistent colour language**: sign colours never reused for series; forecast always the same hue; fair value always
   the same line style.
6. **Grouped tables with counts and explanations** (Overvalued 7, Stale 5, Undervalued 3) instead of one long list; a
   good answer to the plan's "pages are too long" problem.
7. **Side drawer on row click**, so the table stays in place.
8. **Calendar with typed dots** for ex-dividend, payment and estimated events; one calendar for earnings dates too.
9. **Plain-English screen builder that fills visible filter chips**, so the user can correct the parse.
10. **Printing the calculation chain** for any model-derived value (the narrative valuator does this well).

## 8. Things to avoid

- Raw or wrongly signed figures with no explanation (the stock page showed interest coverage of -705.6x).
- Two valuations on one screen without saying which is the headline (DCF said overvalued while the headline chip said
  undervalued on analyst consensus).
- A score of zero for a category that does not apply or is merely low (Microsoft's dividend 0/6).
- Heavy chrome on very wide tables; the holdings and returns tables scroll horizontally on small widths.
- Gating basic results behind a paywall; irrelevant for a single-user tool but a reminder that "Top 4" lists feel broken.
- Pop-ups over the page after scrolling; a cookie banner and a sign-up modal repeatedly covered content during the review.
