# Simply Wall St: feature and analysis review for StewardAI

Reviewed 2026-10-15 by driving the live site with Playwright, logged out and then logged in on a fresh free account
(which starts with a 7-day Premium trial). Companion file: `simplywallst_visualization_catalog.md` (how it displays things).

> **Corrections after a code audit (2026-10-15).** Some items below understate what StewardAI already has. Existing:
> the `company_valuation_scenario` DCF with reverse DCF, bear/base/bull and a sensitivity grid; payout, shareholder-yield,
> net debt/EBITDA, interest-coverage and accruals ratios; a correlation matrix in the risk view; the `corporate_action`
> table with a weekly Twelve Data dividend job and Trading212 dividend transactions; saved-screen new-match alerts
> (`ops/opportunities.py`); `composite_qvm` and `red_flags`; histogram, sankey and waterfall helpers in `lib/charts`.
> The build plan (`docs/plans/insight_features_implementation_plan.md`, §2) extends these rather than rebuilding them.

## 1. What was and was not covered

Covered, with screenshots and extracted page text:

- Logged out: home page, demo portfolio (Holdings, Analysis, Valuations, Dividends, Returns, Updates), the full stock report
  for Microsoft (Overview, Valuation, Future, Past, Health, Dividend, Management, Ownership), the screener (including the
  advanced-filter dialog and the natural-language screen builder), Discover investing ideas, a community narrative and its
  valuation calculator, plans and pricing.
- Logged in: dashboard, the new Analysis tab (Scorecard, Valuation, Future, Past, Health, Dividends, Diversification,
  Correlation, Risk), holdings views, watchlist, notifications, portfolio settings, add-holding dialog, risk-check dialog.

Not covered, so nothing below is claimed about them:

- Charlie AI (it is a separate site, `charlieinvest.ai`) and the stock-page "Ask anything" answers.
- Broker sync, AI transaction import, price alerts and screener alerts (all need a real portfolio or a paid plan).
- Drag behaviour of the interactive Snowflake filter (plain clicks did not change the results).
- The column sets of the Income, Growth, Value and Snowflake holdings views (only Fundamentals was read; the others are
  named on the pricing page). The `/dividend-calculator` URL returned an empty page.
- Mobile layout and the iOS app.
- The demo portfolio is synthetic and the account I used holds six tickers with no cost data, so portfolio screens that need
  transactions were read from the demo.

I did not change anything in the account except logging in (and one natural-language screen, which saves nothing).

## 2. How Simply Wall St analyses a stock

Everything is organised around one fixed scheme, which is the main thing worth copying.

**The Snowflake.** Five axes (Value, Future, Past, Health, Dividend), each scored out of 6 from six yes/no checks, so 30
checks in all. The headline is a one-line verdict ("Outstanding track record with flawless balance sheet") plus three
"Rewards" and one or more "Risk" bullets, each a plain sentence with the numbers inline ("Price-To-Earnings ratio (29.6x) is
below the Software industry average (30.7x)"). Every page of the stock report opens with its own `n/6 criteria checks`, then
key figures, then the individual checks as pass/fail sentences. Management has 4 checks, not 6.

**Risk Checks.** A separate list of 14 automated pass/fail questions, each with a section reference. Seen on Microsoft:
substantial insider selling in 3 months (failed), financial position, meaningful market cap, negative equity, concerning
recent events, liquid and stable share price, margin trend, sufficient analyst coverage, meaningful revenue, dilution,
forecast profitability, earnings quality, forecast growth, dividend sustainability. A footnote states a company that passes
all of them is not risk free.

**Valuation (section 1).**
- Discounted cash flow value ("Future Cash Flow Value") against price, shown as a three-band gauge (20% undervalued, about
  right, 20% overvalued) plus a history chart of price against the DCF value.
- Relative value on one key multiple chosen per company (PE if profitable, otherwise PS or PB), shown three ways: against a
  hand-picked peer set (peer average line, earnings growth beside each bar), against the whole industry (histogram with the
  company's bin highlighted and a table per bin), and against a "Fair Ratio" (50.1x for Microsoft, described as the PE
  implied by forecast growth, margins and risk factors; the method is not published in detail).
- A history table of the multiple, and an analyst price-target chart with the consensus line, a high/low band, a 12-month
  forecast cone, the analyst count and an "Agreement" rating based on target dispersion (Good when under 15% spread).
  The target history table also carries a "1Y actual price" column, which turns old targets into a track record.
- Community and analyst "narratives" each carry their own fair value (see section 4).

**Future (2).** Earnings and revenue growth versus savings rate, market and industry, EPS growth, forecast return on equity
in 3 years, analyst coverage rating, and a chart that joins reported history to analyst forecasts with series toggles
(revenue, earnings, free cash flow, cash from operations).

**Past (3).** Earnings growth against the industry, revenue growth, ROE, net margin, a next-earnings date, reported-earnings
updates written as short summaries, and a Sankey of where revenue goes (segments, cost of sales, gross profit, expenses,
earnings) with a year strip going back to 2015. A quarterly history table sits under it.

**Health (4).** Debt to equity (and its 5-year trend), debt coverage by operating cash flow, interest coverage, short-term
assets against short- and long-term liabilities, cash against debt.

**Dividend (5).** Yield against the market's bottom and top quartile and the industry average, buyback yield and total
shareholder yield, dividend growth, payout ratio on earnings and on cash flow, stability and growth of payments, the next
ex-dividend and pay date with a "buy within N days" line, and written dividend updates ("Dividend of US$0.98 announced ...").
The checks penalise low-yield growers: Microsoft scores 0/6 although its payout is 20% and it raises the dividend yearly.

**Management (6) and Ownership (7).** CEO pay, salary share, tenure and ownership, management and board average tenure, leadership
and board tables; insider trades table, ownership by type (institutions 75.1%, general public 24.7%, insiders 0.03%),
top-25 holders with change in position and the holder's share of its own portfolio, and an insider-volume bar.

**Earnings call card.** A short generated summary of the last call with suggested follow-up questions
("What did analysts push back on?", "What would need to be true to justify the price?").

**Data transparency.** Every chart has a `Data` button (underlying table), a `Learn` button, and a footer table of when each
dataset was last refreshed, its timeframe and its public source (SEC 10-K/10-Q, Form 4, 13D/13F, DEF 14A, 8-K, price vendor).
The analyst-source dialog lists every analyst and firm behind the consensus (84 analysts covered Microsoft, 51 supplied
estimates).

## 3. How Simply Wall St analyses a portfolio

**Holdings tab.** Value over time (stacked or combined, with the cost basis line and range chips), total value, 1-day return,
total return, annualised IRR, a returns split into unrealised, realised, dividends and currency, estimated dividends and
yield, the portfolio Snowflake with a one-line verdict, and a table that can be switched between seven views: Check-In,
Holding, Income, Growth, Value, Snowflake, Fundamentals (plus a custom view). Clicking a row opens a side drawer
(Overview, Holding, Updates, Notes). A "Performance vs market" tab compares to a benchmark.

**Analysis tab (new, announced as rebuilt).** Opens with a portfolio **Scorecard**: the same five axes, each averaged over
holdings, with "lifting the score" and "holding it back" lists of the best and worst holdings per axis and their weights.
Then one section per axis, each a ranked dot-strip of holdings against the market and the portfolio average:
valuation (fair value, DCF, PE, PS, PB or narrative fair value), forecast earnings/revenue/EPS growth, ROE/ROCE/ROA, debt to
equity, forecast dividend yield. Then:
- **Diversification**: a Sankey from portfolio to sector to industry to ticker; a concentration bar; revenue by geography.
- **Correlation**: the most and least correlated pairs, and a half-matrix heatmap with 0 as the neutral midpoint.
- **Risk**: one 0-100 "intrinsic risk score" against the S&P 500, then volatility (58.4% against market 12.9%), maximum
  drawdown, top-3 concentration, beta, Sharpe and Sortino, each with a one-line verdict ("Above 3.0, exceptional for the risk
  taken"), and volatility by holding.

**Valuations tab.** Portfolio value against the sum of per-holding fair values over 12 months ("US$378,564 against
US$454,664, 20.1% undervalued"), share of the portfolio over and undervalued, count of stale narratives, weighted aggregate
upside in currency, and a table grouped into Overvalued, Stale narratives (not updated in 90+ days) and Undervalued. A
"quick-assign all" control re-values every holding on one basis (most popular narrative, analyst consensus, DCF) without saving.

**Dividends tab.** Next-12-month income against the last 12 months, monthly income, yield and yield on cost; a 3-year monthly
history chart; largest and smallest contributors; **dividend quality tiers** (low 0-2, medium 3-4, high 5-6) with the income
share in each tier; a forecast of estimated future payments by month or year; a month-grid calendar of ex-dividend and
payment dates; an upcoming-events table.

**Returns tab.** Waterfall from unrealised to realised to dividends to currency to total, contributors to returns, and a
detailed report per holding (shares, price, value, cost basis, unrealised, realised, dividends, currency, total, IRR) with
a "Liquidated holdings" block, an expandable transaction list (buy, sell, split) and a CSV export.

**Updates tab and dashboard feed.** A typed feed (live news, community, earnings, analyst, valuation, dividends, insider
trades, risks, other) in which each card shows ticker, price and 1-day move, and a "what changed" summary.

**Watchlist.** Same table with a narrative column ("My FV and narrative"), last published and last update. It nudges toward the
portfolio ("a watchlist shows you the price; a portfolio shows you the truth").

**Screener and Discover.** About 60 metrics in seven groups (company, valuation, future growth, past performance, financial
health, dividends, management), a Snowflake filter, market and industry pickers, keywords, an "ask in plain English" box that
turned "US dividend payers yielding over 4% with low debt and positive earnings growth" into one market and three metrics
(158 results), saved screens with alerts, a "new companies in the last 7 days" counter, list and card views, and about 50
curated investing ideas (themes such as AI infrastructure or "Spotless management") which are just saved screens with an
editorial blurb and an aggregate Snowflake.

**Plans.** Free: 5 reports/month, 1 portfolio of 10 holdings, results limited to the top 4 per screener, equity curve for 12
months. Paid tiers lift those limits and add broker sync, AI import, export and the seven table views.

## 4. Narratives: the one idea that is not just data

A community or analyst narrative is a short thesis plus a five-input valuator, displayed as a calculation chain.
Example (an NVIDIA narrative): revenue growth 29.55% a year against 52.13% history and 1.88% industry; profit margin; future
PE 28.33x; share count 23.12b; risk-level discount rate 11.26% (a size/volatility/profitability/country default). It prints
`Earnings '31 US$703.85b x PE 28.33x = market cap US$19.94t / 23.12b shares = US$862.39 share price '31, discounted to 2026
at 11.25% = fair value US$505.98`. Owners then follow or set their own fair value and get alerts when the underlying story
changes. Narratives older than 90 days are flagged stale. StewardAI's thesis and valuation pieces are a natural fit for
this; the social layer is not.

## 5. What StewardAI already has (verified in the code, do not rebuild)

StewardAI is already stronger than Simply Wall St on risk, provenance and evidence. Confirmed in `apps/web/src` and
`apps/backend/src`:

| Area | Present in StewardAI |
| --- | --- |
| Risk | Ex-ante risk, factors, tail, stress, what-if, pre-trade, rebalance, validation pages under `/risk`; correlation matrix and rolling benchmark correlation in `risk-view.tsx` |
| X-ray | Concentration, overlap, benchmark, supply chain, fundamentals, proposed trades under `/xray` |
| Performance | Returns, attribution, lots, ledger, core-versus-stocks; monthly deposits, withdrawals, dividends and fees table in `returns-view.tsx` |
| Company workbench | Overview, valuation, estimates, financials, ratios, segments, peers, governance, network, assessment, case, report |
| Disclosures | Insiders, ownership (13F), look-through (N-PORT), deals, network, screen, replay, estimates |
| Discovery | Filter builder, saved screens, compare tray, backtest, provider panel under `/discover` |
| Funds | Factsheet, holdings, performance, style, checklist, overlap, compare |
| Charts | Custom SVG kit: bar, candle, donut, drawdown, hbar, heat, line, radar, revision-line, ring, sankey, sparkline, treemap, vintage-fan, waterfall (recharts only for price chart) |
| Data | SEC XBRL statements (10 years, reconciled), Form 4, 13F, N-PORT, FX, price bars from Stooq/Yahoo/EODHD/Alpha Vantage, FMP profiles and estimates for 13 of 24 operating holdings, peer sets, segment revenue, factor returns, Trading212 portfolio and ledger |

Known data holes (from `docs/data_completeness_report.md`): earnings facts are thin, thesis verdicts are zero because no
thesis config is saved, FMP estimates cover only 13 holdings, and three of 30 holdings are funds with little company data.
Several recommendations below depend on those, and say so.

## 6. Recommended features, ranked

Ranking weighs value to a single self-hosted owner, how much of the data StewardAI already stores, and effort. "Data now" is
what exists today; "Gap" is what must be added. I verified the *absence* of a feature by searching the code for the
concept, so check the named files before building.

### Tier 1: high value, data mostly in hand

**1. Forward dividend income, calendar and dividend quality (biggest single gap).**
Simply Wall St's whole Dividends tab has no StewardAI equivalent: the only dividend views are a monthly *received* table and an
ex-dividend line on a company overview that is empty when nothing is stored.
- Build: next-12-month income against the last 12 months; monthly bar history with estimated future months in a different
  colour; largest and smallest contributors; yield and yield on cost per holding; a month-grid calendar of ex-dividend and
  pay dates; an upcoming-events list; income share by quality tier.
- Quality score out of 6 per holding, from checks computable today: earnings payout under about 75%, cash payout under 75%,
  dividend per share not cut in 10 years, 10-year dividend growth positive, yield in the market's upper half, and a forecast
  of at least stable dividends. Show the sentence for each check, not just the number.
- Data now: Trading212 dividends received and lots (yield on cost, realised income), `dividends_paid` and
  `dividends_per_share` in the statement store (`ratio_set.py`), shares held.
- Gap: dividend event history with ex and pay dates. Yahoo's chart endpoint returns dividend events alongside prices and
  StewardAI already reads Yahoo bars; SEC dividend declarations (8-K) give declared dates for US issuers. Estimate undeclared
  payments from the last four, flagged as estimated. Foreign issuers (CSG, Chugai, Daiichi, Nu, ASML) will be partial.
- Respect the lesson in section 7: do not let a low-yield grower score zero without saying why.

**2. Transparent per-holding checklist: five axes, six checks each, plus a risk-check list.**
StewardAI has a fund checklist and a ratio set but no stock-level pass/fail scheme that reads as sentences.
- Build: Value, Future, Past, Health, Dividend, each with six explicit checks and a written result; plus about 14 risk checks.
- Computable now from statements, Form 4, prices and peers: debt to equity and interest coverage, cash against debt,
  current assets against current and long-term liabilities, negative equity, margin trend, ROE against peers, revenue and
  earnings growth against peer median, earnings quality (cash flow against net income, accruals), dilution (shares
  outstanding year over year), insider net selling over 3 months, price stability (weekly volatility against market),
  liquidity (volume), market-cap floor, upcoming earnings date, peer-relative PE.
- Gap: forecast growth checks depend on FMP estimates (13 of 24 holdings); mark those checks "no estimate" instead of
  failing them. Industry averages need the peer set, not a global universe, so label the benchmark as "peer median (n=5)".
- Use the existing thesis KPI verdicts alongside, not instead: the checklist is the objective layer, the thesis the owner's.

**3. Portfolio scorecard and "lifting / holding back" lists.**
- Build: the five axis scores averaged over holdings (weighted by value), the radar from `charts/radar.tsx`, and per axis a
  list of the best and worst holdings with their weight. Click an axis to see the ranked holdings. This is also the natural
  home for item 2's scores and is cheap once they exist.
- Data now: nothing new beyond item 2.

**4. Portfolio valuation against fair value.**
- Build: portfolio value against the sum of per-holding fair values over time, share of the portfolio above and below fair
  value, weighted upside in currency, and a table grouped Overvalued, Stale, Undervalued with a stale flag when the fair
  value input is older than 90 days.
- Data now: DCF and reverse-DCF code and valuation panels exist (`valuation-view.tsx`, `valuation-panel.tsx`,
  `valuation-benchmark-panel.tsx`) and theses carry owner assumptions.
- Gap: a stored fair-value series per holding (one row per date and method) so history can be drawn. Start recording today;
  the step-line history only exists from the first stored value. Offer DCF, PE-based and thesis-based bases and let the
  owner switch the basis for the whole portfolio, as the "quick-assign" control does, without saving.

**5. Scenario fair-value calculator on each thesis (the narrative idea, minus the social layer).**
- Build: five inputs (revenue growth, margin, future PE, share count change, discount rate) over a 5-year horizon, with the
  calculation chain printed line by line and a bear, base and bull set. Show the implied price against the current price,
  and flag the thesis when its inputs are older than 90 days or when reported growth and margin move far from the assumption.
- Data now: revenue, earnings, shares and margins from the statement store, peer PE from the peer set. This is a good fit with
  the reverse-DCF already present.
- Gap: none technical; a small stored assumption record per thesis and scenario.

**6. Typed holdings views (Income, Growth, Value, Snowflake, Fundamentals, Check-In) and a custom view.**
- Build: one table component with named column presets and a column picker; the Check-In preset shows 1-day move, last event
  with date, price against fair value and a 1-year sparkline with the fair value step line.
- Data now: everything is stored. Effort is low and it removes the "holdings table is 72rem wide with every column" problem
  noted in `ux_ui_dataviz_improvement_plan.md`.

### Tier 2: valuable, with data caveats

**7. Valuation against peers and industry, as pictures.**
- Peer bars (company highlighted, peer average marker, growth column), an industry histogram with the company's bin
  highlighted, a PE-history band chart, and a "fair multiple" given growth and margin.
- Data now: peer sets, peer metrics (`peer_metrics.py`), ratio set, XBRL history for PE.
- Gap: the industry histogram needs a wider universe than 5 peers. The 13F, N-PORT and SEC company lists give US issuers for
  free, but this means ingesting statements for hundreds of companies; do it only if the screener universe grows. A fair-ratio
  regression across peers is feasible but statistically thin with small peer sets, so show confidence or skip it.

**8. Analyst consensus with agreement and target track record.**
- Build: consensus target, high and low, analyst count, dispersion rating, and a chart of target against subsequent price.
- Data now: `public_estimate_snapshot` and `estimate_value` exist; FMP covers 13 holdings.
- Gap: history. Snapshot consensus daily from now on; the track-record column only fills over time. Be explicit that
  coverage is partial; the plan already calls broad consensus unavailable on free data.

**9. Natural-language screener.**
- Build: a text box that turns a sentence into the existing filter-builder JSON, shows the parsed filters for editing and the
  result count. StewardAI already runs OpenAI and Google models, has `discover/filter-builder.tsx` and saved screens.
- Add the useful Simply Wall St extras: a "new matches in the last 7 days" counter on saved screens and an alert when a
  holding or watchlist name starts to match.
- Gap: metric parity. Simply Wall St exposes about 60 filters including forward PE, EV/EBITDA, net debt to EBITDA, insider
  buying ratio, buyback yield and total shareholder yield; compare against `discovery/metrics.py` and add the missing ones
  that statements already support (buyback and shareholder yield, net debt to EBITDA, ROCE, margins, share dilution).
- Guardrail: the model must output a validated filter object, never run free text against the database.

**10. Returns breakdown with currency effect, and liquidated holdings.**
- Build: a waterfall of unrealised, realised, dividends, currency and total; a per-holding report with IRR; a separate block
  for closed positions; expandable transactions with splits; CSV export.
- Data now: ledger, lots, FX rates and attribution pages exist. A search found some currency-effect handling, so first check
  whether currency is isolated as its own bar. Closed positions and split rows are likely already in the ledger.

**11. Diversification views.**
- Build: sector to industry to ticker Sankey (the Sankey component exists), revenue by geography across the portfolio, and
  the top-3 concentration statement.
- Data now: `segment_revenue` and `company_segment_map`. Gap: geography coverage is only as good as XBRL segment tagging;
  show coverage ("geography known for 62% of value").

**12. One headline risk score with plain verdicts.**
- StewardAI's risk pages are deeper than Simply Wall St's, but a single 0 to 100 number with a gauge against the benchmark,
  and a one-line verdict under Sharpe, Sortino, beta, drawdown and top-3 concentration, would make the overview faster to
  read. Pure presentation over existing numbers; define the score transparently (which inputs, which weights).

### Tier 3: smaller or conditional

- **Typed updates feed.** Chips for news, earnings, analyst, valuation, dividends, insider, risks, other on the inbox, with a
  consistent card (ticker, price, 1-day move, what changed). StewardAI has news, inbox and alerts; this is a filter and a card
  design.
- **Earnings-call summary with suggested questions.** Only useful if transcripts or call summaries are available free;
  StewardAI has few mentions of transcripts. IR releases and 8-K exhibits are available, so a summary of the earnings
  release with "what to ask" prompts is possible; a transcript is not.
- **Ownership breakdown by holder type** with the top-25 share and each holder's position change, beside the 13F pages.
  Data now: `institutional_holding`, `insider_transaction`. Easy.
- **CEO pay, tenure and ownership** from DEF 14A; check what `companies/[ticker]/governance` already shows first.
- **Data provenance footer** (dataset, last update, source form) on every page, plus a `Data` toggle that shows a chart's
  table. StewardAI already has evidence ids, `known_at` and a data-sources page, so this is mostly a consistent component.
- **Watchlist with a thesis/fair-value column** and an "evaluate this like a holding" action, since the current watchlist page is
  noted as a wide per-row-form table.

## 7. What not to copy

- Social features: community narratives, likes, follows, author programme. A single-user tool has no audience.
- Broker sync for 2000 brokers. StewardAI reads Trading212; add another only if the owner holds one.
- The gamified setup guide, upsell banners, ads, or limits on results per screener.
- Scoring that hides its own flaws, seen on the live site: Microsoft shows interest coverage of -705.6x, a raw number with the
  wrong sign for a company that earns net interest; the dividend score is 0/6 for a 20% payout that grows each year because
  yield is below the market bottom quartile; the DCF says 7.9% overvalued while the same page's headline says 6.6% undervalued
  on consensus. The lesson is to state the basis next to every verdict, handle "not applicable" explicitly, and never show
  two valuations without saying which is which.
- The Snowflake as a proprietary-looking ring is a branding choice; the transferable idea is the fixed five-axis, six-check
  scheme with sentences, not the shape.

## 8. Suggested order

1. Dividend income, calendar and quality (item 1): largest gap, data mostly present.
2. Per-holding five-axis checklist and risk checks (item 2), then the portfolio scorecard (item 3) on top of it.
3. Fair-value series and portfolio valuation (item 4) and the scenario calculator (item 5), sharing one stored assumption
   record.
4. Holdings view presets (item 6), natural-language screener (item 9), returns waterfall review (item 10).
5. Peer and industry pictures, analyst agreement, diversification and risk-score presentation as the data allows.

Start recording the data that has no history yet (fair values, consensus snapshots, dividend events) immediately, because
those charts only get richer with time even if the screens ship later.
