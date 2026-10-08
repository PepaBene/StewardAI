# StewardAI analytics methodology

Method specification for plan task `M0.2`. Code: `apps/backend/src/steward/analytics/`. Golden examples (derived by hand, never by the calculators): `apps/backend/tests/golden/analytics/cases.json`, run by `tests/test_analytics_golden.py`. Versions: `analytics/versions.py`. A saved result keeps the version it was computed under.

## 0. Conventions

- **Where figures are computed**: backend code from versioned, dated observations. The browser formats; an LLM may only explain a figure already computed, citing its inputs.
- **Result envelope** (`analytics/contract.py`): `value`, `unit`, `as_of`, `known_at`, `method_version`, `sources`, `coverage` (numerator/denominator and basis), `warnings`, `status` (`complete`, `partial`, `unavailable`, `paid_only`, `disputed`), `input_hash`, optional `explain` (exclusions, formula, citations, gaps). `null` is unavailable; it is never zero or a guess.
- **Numbers**: `Decimal` end to end. Full precision inside a calculation; one rounding at the edge, banker's rounding (`ROUND_HALF_EVEN`), places by unit: fraction 8, ratio 6, multiple 4, money 2, per share 4, percentage points 4, days/count 0. Percentages are fractions (0.1234 = 12.34 %).
- **Day count and annualisation**: actual/365 for dated cash flows (XIRR) and for converting a window to years; 252 trading days (daily), 52 (weekly), 12 (monthly) for volatility-type statistics.
- **Currency**: a minor unit (GBX, GBp, ZAc, ILA) is converted to its major currency before any comparison (`to_major_unit`). Two endpoints in different quote units never produce a return.
- **Accounting basis**: US GAAP and IFRS statements are never mixed in one ratio or median; each period carries its basis.
- **Negative/missing inputs**: a ratio whose denominator is zero, negative or meaningless is `n/m` with the reason (`ratios.py`); a missing input is "missing X". Neither becomes zero.
- **Point in time**: "as known at T" is the latest revision with `known_at <= T` (`pit.py`), so a restated filing or revised macro release does not rewrite what was seen earlier.

## 1. Security price return

`(end / start) - 1` for one listing, one quote unit and one adjustment basis. Raw closes are first put on the end-date share basis with the known splits (`price / product of split ratios taking effect after the date`). A flagged split without a ratio, mixed quote units, a non-positive price or a reversed window yields `unavailable`.

## 2. Security total return

Daily factor `(close_t + dividend_t) / close_{t-1}`, chained; each dividend is reinvested at the close of its ex-date (`reinvest_at_ex_close`); an ex-date on a non-trading day is paid at the next close. Dividends are put on the final share basis like prices. Dividend currency and the FX rate used are stored with the input. If the distribution history is incomplete no total return is published, the price return over the same window is shown instead and flagged.

## 3. FX translation and CAGR

A base-currency return uses the FX rate of each valuation date (the rate observation's own date and source stored). CAGR is `(end/start)^(1/years) - 1` with years = days / 365; a non-positive start or end is `n/m`.

## 4. Portfolio time-weighted return (TWR)

Convention `end_of_day_flow`: a flow lands at the close of its day. Subperiod return `r_i = (V_i - F_i) / V_{i-1} - 1`, TWR `= product(1 + r_i) - 1`. Only external flows (deposit, withdrawal, transfer) are `F`; dividends, fees, taxes and trades are internal. A subperiod with a non-positive opening value is skipped and counted in coverage. A complete figure needs the missing material weight (positions of 1 % or more with a missing price, flow or action) to be zero (`thresholds.full_portfolio_return_allowed`); otherwise no value is published.

## 5. Money-weighted return (XIRR)

The annual rate at which dated owner flows plus the ending value sum to zero: `sum(a_i / (1 + r)^((d_i - d_0)/365)) = 0`. Sign convention: money the owner pays in is negative; withdrawals and the ending value are positive. Solved by bisection on [-99.99 %, +10 000 %] (tolerance 1e-12). No outflow or no inflow, one date, or no sign change of NPV across the bounds means no stable root: `unavailable`.

## 6. Return attribution

`gain = end value - start value - net external flows`. Components: local price, income, FX, trading and fees on one valuation base. `residual = gain - sum(components)` is disclosed, never absorbed, so components + residual reconcile to the gain. Market value with missing actions or stale FX is reported as the unattributed share and makes the bridge `partial`.

## 7. Fund look-through

Direct holding: its market value. Fund holding: `fund value x underlying weight`. Aggregate by canonical issuer (not by listing or share class unless chosen), keeping every path (direct, fund by fund). Cash, derivatives and the fund weight the published rows do not cover (`1 - sum(rows)`) are separate buckets. A fund without holdings is wholly unknown. Unknown weight is never spread over known issuers. Buckets sum to total portfolio value.

## 8. Two-fund holdings overlap

After issuer normalisation: `sum over shared issuers of min(w_A,i, w_B,i)`. Each fund's covered fraction (sum of its issuer weights) is reported; below 90 % flags. Differing holdings dates warn; a date more than 45 days behind the newer fund warns as stale. If either fund has no holdings, overlap is `unavailable`, never zero.

## 9. Concentration and portfolio fundamentals

`HHI = sum(w_i^2)` on weights as fractions of one portfolio value date; effective count `1 / HHI`; top-10 weight. The unknown bucket is shown beside, outside the HHI. Weights of different as-of dates warn.
Portfolio earnings yield: `sum(w_i x E/P_i) / sum(w_i)` over covered positive-earnings equities; P/E is `1 / yield` only when the aggregate yield is positive. Cash, derivatives, negative or missing earnings and incompatible financials (banks mixed with industrials) are listed as exclusions; coverage is covered weight over total weight; the scope (with or without look-through) is stated.

## 10. Valuation: DCF and reverse DCF

`EV = sum fcf_t / (1+r)^t + fcf_N (1+g) / (r - g) / (1+r)^N`, requiring `r > g`. Equity `= EV - net debt - minorities`; per share on the diluted count. Net debt, minorities, diluted shares and a forecast are inputs of the owner's scenario with provenance; any missing blocks a per-share figure. Margin of safety `(value - price) / value` only against a user-selected scenario. Reverse DCF solves the constant FCF growth (over N years, from a positive base FCF) that equates value to price on [-50 %, +100 %]; it is an assumption, not an observation. No model-generated analyst estimate may stand in for missing consensus.

## 11. Risk statistics

Aligned simple returns at a stated frequency, explicit risk-free series, stated sample size. Volatility: sample stdev x sqrt(periods per year). Downside deviation: `sqrt(sum(min(r - MAR, 0)^2) / n)` x sqrt(periods per year), over all n. Max drawdown: worst fall of compounded wealth from a running peak. Beta: sample cov / var of aligned returns. Sharpe: mean excess / stdev of excess x sqrt(periods per year). Historical VaR/ES at confidence c: sort ascending, `k = ceil(n(1-c))`; VaR = minus the k-th smallest, ES = minus the mean of the k smallest; both are descriptive estimates. Minimum samples (`thresholds.py`): volatility/downside/Sharpe/beta 60, drawdown 20, VaR/ES 250; below them the value is suppressed.

## 12. Fundamental ratios

One library (`analytics/ratios.py`) defines each ratio's formula, period rule and exclusions; screens, peer compare and ratio pages read it. Current entries: FCF yield `(CFO - |capex|) / market cap`; earnings yield `net income / market cap`; EV/EBIT `(market cap + debt - cash) / operating income` (EBIT > 0); gross profit / total assets; accruals `(net income - CFO) / average total assets`; share-count change over 1 and 3 years (3-year annualised `(1+x)^(1/3) - 1`); asset growth; cash runway `cash / trailing cash burn` only when CFO < 0 (a self-funding company has no runway figure, not infinite); 12-1 momentum `close(t-1m) / close(t-12m) - 1` on stored adjusted closes, each endpoint within 7 days of its date. The existing six filed-statement ratios (`peer_metrics.py`: gross margin, operating margin, ROIC with the stated invested-capital formula, net debt/equity, FCF conversion, revenue growth) keep their definitions and are the first entries to be merged into this library in `M2.15`.

## 13. Peer sets and screens

A sector label alone never defines peers. A peer median needs at least 5 comparable peers on the same accounting basis, duration within 10 days and period end within 45 days; otherwise no median is shown and the reason lists each excluded peer. A screen filters on a metric only when its published definition and as-of period match the user's choice; a missing metric is `unknown`, never a passing zero; every universe member is accounted for as matched, failed, unknown or without data.

## 14. Cross-listings

ADR vs ordinary: `ADR price / (ordinary per ADR x local price x FX) - 1` in the ADR currency. A missing ratio or FX rate blocks a merged price chart.

## 15. Portfolio ledger, windows, lots and sleeves (Phase 4)

**Ledger** (`steward/performance/ledger.py`). Each stored Trading212 transaction becomes one entry with a signed `cash_effect` in the account currency: a buy is negative, a sale and a dividend positive, a deposit positive, a withdrawal negative, a fee negative, a transfer keeps the sign reported. Fill fees follow `fee_basis`: `included` means the net value already contains them; `excluded` and `unknown` add them to a buy and subtract them from a sale. Only deposits, withdrawals and transfers are external flows. A subtype the ledger does not know is `unsupported`; an entry in another currency than the account's is kept but never summed (`needs_fx`). The day of an entry is its Prague calendar date, the boundary the daily snapshots use.

**Reconciliation.** Cash between two snapshot days is `(total value - invested value)` at each end; the ledger entries after the opening day and up to the closing day must explain the change within a tolerance (the larger of the owner's tolerance and 0.01 % of the value). Held units rebuilt from fills are compared with the latest broker position snapshot; a difference is reported with its likely cause, never netted away.

**Windows.** 1M, YTD, 1Y, 3Y, 5Y and since the first snapshot, on the daily value series. The opening value is the last snapshot on or before the window start (not more than 7 days earlier); a window the history does not reach is `unavailable` with the date the history starts. TWR follows section 4 and XIRR section 5 on the opening value, the window's flows and the ending value. A window of 365 days or more is annualized as `(1 + r)^(365 / days) - 1`; a shorter one is not. A precise TWR or XIRR is withheld when any snapshot interval inside the window has a cash change beyond the tolerance that no stored transaction explains, because an unrecorded deposit would distort it; the message gives that unexplained amount as a share of the ending value.

**Benchmark.** `active = (portfolio TWR - benchmark return) x 100` percentage points over identical dates, on closes at or within 5 days before each endpoint. A benchmark quoted in another currency than the portfolio's base, or with an unknown currency, gives no active return; a price index or an unknown dividend basis gives one with a warning. A blend is indexed to 100, drifts with its components and resets to its weights on the first day of each month, quarter or year, or never.

**Lots.** Each purchase is a lot at its cash cost, fees included. A sale consumes lots oldest first (FIFO), or is costed at the pooled average (the lots and their dates stay FIFO for the holding period). A sale larger than the units held is `unmatched` and carries no gain. Splits the owner enters (ticker, first day of the new count, ratio) restate every earlier fill of that ticker: quantity x ratio, price / ratio, the money unchanged, so the broker's own post-split positions and the fills agree; a fill on the split day itself is taken as already in the new count. A merger the owner enters (old ticker, new ticker, first day, new shares per old) is a share exchange, not a sale: earlier fills of the old ticker are carried to the new one with quantity x ratio and price / ratio, and the money, the purchase date and so the lot's age are unchanged (cash for a fraction of a share is not modelled). A spin-off the owner enters (parent, child, first day, child shares per parent share, share of cost moved) applies at the start of its day to every lot still open: the child gets `ratio` units per parent unit and `cost share` of the lot's cost, keeps the parent lot's purchase date, and the parent lot keeps the rest of its cost; under average cost the pools move by the same amounts. The cost split comes from the company's own notice, which the owner types; whether the purchase date carries over is the owner's tax question. Fills on the spin-off day are not entitled to the child shares. Actions are applied in date order, so a split after a merger restates the merged shares. Corporate actions not entered are not applied; they appear as position breaks. **Holding period.** A lot passes the test on the first day after the `n`-year anniversary of its buy date (`n` set by the owner, default 3). A lot whose ticker has reached its thesis sell target and passes within the owner's warning months (default 6) is flagged. The annual exemption total is gross sale proceeds, converted to the limit currency at the stored rate of the sale date, from the part of each sale taken from lots still inside the test on the sale date. These are dates and owner thresholds, not a tax liability or filing advice.

**Fills of a holding with no snapshot row.** A position bought and sold between two snapshots, or a sale that closes a position, has no row on the day to take its rate from. Its own rate on the nearest snapshot day it appears is used (the earlier day on a tie). A holding that never appears in a snapshot has no rate: attribution leaves it unattributed and says so, and the sleeves leave its flow out.

**Attribution.** Per holding and valuation day, in the base currency: price effect `(q1 p1 - q0 p0 - sum(q_f p_f)) x fx1`, FX effect `q0 p0 (fx1 - fx0)`, with prices and units in the listing's own currency and fills `q_f` at `p_f`. Dividends are income; fill fees and cash fees are costs. The window is the sum of daily intervals; `gain = end - start - external flows` and `residual = gain - sum(components)` is shown. A holding whose units changed without a stored fill is excluded from price and FX, and its end value is the unattributed share. Groups (currency, country, strategy classifications) sum the holdings; a holding with no label is `Unknown`.

**Against the benchmark.** For a holding held at the start of the window, `benchmark equivalent = opening value x benchmark return over the same dates` and `active = total contribution - benchmark equivalent`, in base-currency money; the actives sum to the opening holdings' gain less what the benchmark would have made on them. A holding bought during the window has no opening money and gets no figure, and an unattributed holding gets none. It is not a Brinson attribution: the benchmark's weights are not known, so allocation and selection are not separated. It is like for like only when the benchmark is quoted in the base currency and includes dividends; otherwise the page says which.

**Freshness.** The returns, attribution and Core versus stocks pages report the age of the latest position snapshot (the prices and exchange rates used) and, where a benchmark is compared, of its latest close, and flag either past 5 days. The X-Ray exposure panel does the same for its holdings snapshot (against the chosen date when one is picked).

**Sleeves.** Core = the tickers the owner lists; stocks = every other held ticker; cash belongs to neither. A sleeve's daily return is `(V1 - flow) / V0 - 1` with `flow = net buys at fill value - dividends + fees`, so a purchase inside the portfolio is not a gain and a fee counts against the sleeve. The invested portfolio's interval return equals the opening-value-weighted sum of the two (checked on the data). The comparison is the annual gap of the core over the stocks across the rolling window (default 2 years); under 365 days of history it is shown as `too early to judge` and raises nothing; over it, a gap above the owner's limit (default 2 points a year) is a review item.

## 16. Portfolio X-Ray (Phase 5)

**Leaves.** Every direct holding and every published fund row is a leaf weighted as a share of total portfolio value (cash included). Remainders that are not securities (cash, a fund's own cash and derivatives, a fund not looked through, the part of a fund its rows do not account for) are leaves too. Leaf weights sum to `1 + over_reported`, as in the fund aggregate. Unknown weight is never redistributed.

**Classification basis.** Each label is reported, inferred (provider sector mapped to GICS through an explicit table), owner-assigned or none. Country labels carry a kind (country of risk, listing country, home market, owner, unstated); a view that mixes kinds says so.

**Domicile and listing country.** Two lenses with one meaning each, kept apart from the country view. Domicile is the data provider's country when a profile is stored (reported), otherwise the country prefix of the ISIN, which is where the security was registered (inferred). Listing country is the country of the exchange the provider names, through a stated table of exchange names; an exchange not in the table stays unclassified.

**Concentration.** HHI and effective holdings from `analytics.exposure.concentration` on shares of the identified weight at security, issuer, sector, country and fund level; unknown weight is reported beside, not inside.

**Fundamentals.** P/E is the inverse of the weighted earnings yield; P/B is a harmonic mean; dividend yield a weighted mean. Profitability and leverage exclude financial companies (by sector label). Leverage is the weighted mean of net debt to equity and, separately, of net debt to EBITDA; a company with non-positive EBITDA has no net-debt-to-EBITDA and is listed as left out, not set to zero. Each figure reports coverage of equity, is flagged thin under 50%, lists what was left out and gives a reason when withheld.

**Benchmark.** The proxy is a configured fund's equity rows renormalised: today's holdings, not historical membership. Sector is compared only when every label on both sides is GICS; country only when every material portfolio country kind equals the proxy's country basis; otherwise withheld with a reason.

**Owner-entered inputs.** Three things no provider here supplies are typed by the owner, each with a source address and the date it describes, stored append-only (the newest entry per name is in force): *regional splits*, *supply-chain links* and *index weights*. They are always labelled as the owner's, never as official data.
- A *regional split* spreads a reported region (for example "Europe") over countries, with shares summing to 1 (within 0.005). In the revenue-country lens the company's reported share for that exact region name is counted in those countries, on the `owner` basis; a region without a split stays "no data", and a country named outright and also inside a region becomes one slice.
- The *supply-chain graph* draws only entered links (supplier, customer, product, optionally the customer's share of the supplier's revenue). Each company is placed against its look-through weight, matched by ticker symbol; a link where the portfolio holds both ends is listed with the weight held together. Columns are the longest path from a pure supplier, and a loop is cut where it closes.
- *Index weights* give the active share, `1/2 x sum |portfolio weight - index weight|` over equity (funds opened up, renormalised to 1), with names matched by ISIN and then ticker symbol; a pasted name that matches no holding counts at its index weight. When the pasted weights cover under 97% of the index the figure is called approximate, because names left out would move it either way.

**Filled automatically, labelled honestly.** Nothing is invented. Each input has an origin: `owner` (typed), `derived` (computed from stored fund holdings) or, for supply links, `filing`.
- *Derived index weights and regional splits* are a configured fund's stored equity rows renormalised to 1; the source address is that fund's public product page (the iShares and Vanguard pages for the two configured funds were opened and matched to their ISINs on 7 Oct 2026; a fund with no known page falls back to the issuer data address) and the date is the holdings snapshot date. They are the fund's holdings, not the index provider's own file, and say so in their note. A regional split derived this way spreads the region the way the named fund spreads its equity.
- The look-through job refreshes derived inputs after each run (new holdings produce a new row; identical ones are skipped). A newer owner-typed entry is never overwritten by a derived one.
- *Filing supply links* are the confirmed, non-anonymous customer and supplier disclosures already stored from annual reports, for held symbols, newest period per pair, with the filing's address and fiscal period. The customer's share is used only when it is stated as a share of the issuer's revenue. An owner-typed link for the same pair overrides the filing one. Filing links cannot be removed, only overridden.
- Supply-chain weights match holdings by ticker symbol, so a fund row the issuer publishes without a ticker does not count toward a company's weight.

**Funds against their benchmarks.** For each held fund (the 40 largest), the annualised tracking difference of its listing against the benchmark the owner recorded for it, the same figure the fund's performance page shows, only when both are on the same basis (price or total return) and currency; otherwise the reason. The weighted average is over the funds that have a figure, and the covered weight is stated. The benchmark proxy fund is listed too, with its own tracking difference when a benchmark is recorded for it. This is fund against its own benchmark, not the portfolio against an index.

**Proposed portfolio.** Weights are shares of the current total; unspecified holdings keep their weight; cash is the remainder; more than 100% is refused. The proposal is applied to an in-memory copy and nothing is written. A saved what-if stores only the proposed weights and is run against the holdings of the day it is opened.

**As of a date.** With a date, the holdings are the latest daily snapshot on or before it, fund holdings are the newest snapshot fetched by the end of that day, and statements are those known by then. Issuer identity, sector labels, fund charges and the revenue split are the current ones (not stored historically), and the result says so.

**Risk limits.** The overlap, sector and country limits compare one X-Ray bucket's weight (funds looked through, issuers merged across funds) with the owner's ceiling. Only classified buckets are tested: unknown weight is never a breach.
