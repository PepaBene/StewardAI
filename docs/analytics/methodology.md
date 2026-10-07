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
