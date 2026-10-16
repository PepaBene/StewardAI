# Company checks

Company checks are named pass or fail tests, grouped into five scored axes (value, future, past, health, dividend) and a separate list of risk checks.

## What it shows

Each check compares one figure with a threshold and returns one of four states. Each scored axis has six checks and a score from 0 to 6. Risk checks are a separate list: pass means the risk is absent. Risk checks do not feed an axis score.

| State | Meaning |
| --- | --- |
| pass | The figure meets the test. |
| fail | The figure does not meet the test. |
| na | The check does not apply to this company (a dividend check for a non-payer, a balance sheet check for a bank). |
| unknown | An input is missing. The reason is stated. |

## Inputs

- Filed statement figures for the latest fiscal year and prior years (revenue, net income, equity, debt, cash, cash flow, interest, assets).
- Ratios as the Ratios tab shows them, with the peer median, peer count and peer scope.
- Price, base DCF value, the owner's saved fair value and the consensus target, when stored.
- Stored analyst estimates (next-year EPS and revenue, analyst count) and peer forecast growth.
- Dividend history per share, the payer flag and years without a cut.
- Insider filings, market cap, turnover, one-year volatility, listing age, cash runway, filing amendments, next report date, debt due within a year and customer disclosures.
- The risk-free rate (the check input defaults to 4 %).

Every number is read from stored data. A missing input makes the check `unknown` with the reason, never a guessed value.

## Method

Each comparison is one of these, using the stated direction:

```
pass when value >= threshold   (or >, <=, < as stated per check)
unknown when the value or the threshold is missing
```

An axis score counts passes over applicable checks only:

```
applicable = passes + fails
score = 6 * passes / applicable          rounded to 2 decimals
coverage = applicable / (applicable + unknown)
```

`na` and `unknown` checks stay out of the denominator. Unknown lowers coverage, never the score. An axis with no applicable check has no score (shown as not applicable, or no data when some checks are unknown). A check marked informational is shown but never counted.

Peer comparisons need at least 5 comparable peers. With fewer, the check is `unknown` and says how many peers there were.

### Value axis

| Check id | What it compares | Pass when |
| --- | --- | --- |
| `value_multiple_vs_peers` | P/E, else EV/EBIT, else EV/sales (first with a positive value) against the peer median | multiple at or below peer median |
| `value_price_vs_dcf` | price against the base DCF value | price at or below DCF value |
| `value_price_vs_owner` | price against the owner's saved fair value | price at or below fair value |
| `value_peg` | PEG against the limit | PEG at or below 1.5 |
| `value_fcf_yield` | free cash flow yield against risk-free rate plus margin | yield above 4 % + 1 % |
| `value_consensus` | price against the consensus target | price at or below target |

### Future axis

| Check id | What it compares | Pass when |
| --- | --- | --- |
| `future_earnings_vs_risk_free` | next-year EPS growth over trailing EPS against the risk-free rate | growth above the rate |
| `future_earnings_vs_peers` | the same EPS growth against peer forecast EPS growth | growth above peers |
| `future_revenue_vs_peers` | next-year revenue growth over trailing revenue against peer forecast revenue growth | growth above peers |
| `future_eps_positive` | next-year EPS against trailing EPS | next-year EPS above trailing EPS |
| `future_roe` | next-year EPS x diluted shares / latest equity against the floor | at or above 15 % |
| `future_analysts` | analyst count against the minimum | at least 5 analysts |

Growth is `next / trailing - 1` and exists only when the trailing figure is positive.

### Past axis

| Check id | What it compares | Pass when |
| --- | --- | --- |
| `past_earnings_growth` | 5-year diluted EPS CAGR against zero | above 0 |
| `past_earnings_vs_peers` | 5-year EPS CAGR against the peer median | above peer median |
| `past_roe` | return on equity against the floor | at or above 15 % |
| `past_roce` | return on capital employed against the floor | at or above 15 % |
| `past_margin_up` | latest net margin against the year before | latest is higher |
| `past_earnings_quality` | operating cash flow against net income | cash flow at or above net income |

For `past_earnings_quality`, a cash flow below net income still passes when the accruals ratio is below 10 %. When cash flow or net income is not filed, the check falls back to accruals below 10 %.

### Health axis

| Check id | What it compares | Pass when |
| --- | --- | --- |
| `health_short_term_assets` | current assets against current liabilities | assets above liabilities |
| `health_long_term_liabilities` | current assets against long-term liabilities (total minus current) | assets above liabilities |
| `health_debt_to_equity` | debt to equity against the limit | below 40 %, or above it but lower than five years earlier |
| `health_cfo_covers_debt` | operating cash flow over total debt | at or above 20 % (a company with no debt passes) |
| `health_interest_coverage` | EBIT over interest, in times | at or above 5 |
| `health_cash_vs_debt` | cash against debt, else net debt over EBITDA | cash at least debt, else net debt below 3.0 times EBITDA |

### Dividend axis

| Check id | What it compares | Pass when |
| --- | --- | --- |
| `dividend_payout_earnings` | dividends paid over net income | at or below 75 % (fail when earnings are not positive) |
| `dividend_payout_cash` | dividends paid over free cash flow (operating cash flow minus capex) | at or below 75 % (fail when free cash flow is not positive) |
| `dividend_no_cut` | years without a cut | at least 10 years |
| `dividend_growth` | 10-year dividend per share growth against zero | above 0 |
| `dividend_yield_vs_peers` | dividend yield against the peer median | at or above peer median |
| `dividend_shareholder_yield_covered` | total shareholder yield against free cash flow yield | shareholder yield at or below FCF yield |

### Risk checks

| Check id | What it compares | Pass when |
| --- | --- | --- |
| `risk_insider_selling` | net insider sales in the last 90 days | no net selling |
| `risk_dilution` | one-year share count change | at or below 5 % |
| `risk_negative_equity` | equity | above zero |
| `risk_interest_coverage` | EBIT over interest | at or above 5 times |
| `risk_accruals` | accruals ratio | below 10 % |
| `risk_margin_decline` | latest operating margin against three years earlier | latest at or above it |
| `risk_liquidity` | average daily turnover | at or above 1,000,000 (listing currency) |
| `risk_volatility` | one-year volatility against 1.5 times a 16 % benchmark (24 %) | at or below that cap |
| `risk_small_cap` | market cap | at or above 2,000,000,000 (listing currency) |
| `risk_negative_fcf` | free cash flow of the last three years | not negative in all three |
| `risk_debt_wall` | debt due within a year over total debt | at or below 25 % |
| `risk_customer_concentration` | largest customer share | at or below 10 % |
| `risk_restatement` | filing amendments in the last 12 months | none |
| `risk_earnings_soon` | next report date | not within 14 days (informational) |
| `risk_asset_growth` | total assets growth in a year | at or below 30 % |
| `risk_young_listing` | years since first trade | at least 2 years |
| `risk_short_runway` | cash runway in years | at least 1 year |

## Thresholds and defaults

All limits are config values (domain `checks`), versioned and editable. Asset growth, young listing and short runway use the same limits as the Discover red flags.

| Limit | Default |
| --- | --- |
| `payout_limit_earnings` | 0.75 |
| `payout_limit_cash` | 0.75 |
| `roe_floor` | 0.15 |
| `roce_floor` | 0.15 |
| `forecast_roe_floor` | 0.15 |
| `interest_coverage_min` | 5 |
| `debt_to_equity_max` | 0.40 |
| `net_debt_to_ebitda_max` | 3.0 |
| `cfo_to_debt_min` | 0.20 |
| `dilution_max` | 0.05 |
| `accruals_max` | 0.10 |
| `peg_max` | 1.5 |
| `fcf_yield_margin` | 0.01 |
| `analyst_count_min` | 5 |
| `dividend_yield_floor` | 0.01 |
| `dividend_growth_years` | 10 |
| `no_cut_years_min` | 10 |
| `small_cap_max` | 2,000,000,000 |
| `liquidity_floor` | 1,000,000 |
| `volatility_vs_benchmark_max` | 1.5 |
| `benchmark_volatility` | 0.16 |
| `debt_maturity_share_max` | 0.25 |
| `earnings_window_days` | 14 |
| `insider_window_days` | 90 |
| Minimum peers for a median | 5 |
| Customer concentration limit | 0.10 (fixed in code) |
| Asset growth, young listing, short runway | 0.30, 2 years, 1 year (Discover red flag rules) |

## When it cannot be computed

- `na`: all six dividend checks for a non-payer; yield versus peers when the yield is below the 1 % floor; for banks and insurers the checks `health_short_term_assets`, `health_long_term_liabilities`, `health_cfo_covers_debt`, `health_cash_vs_debt`, and `past_roce` when it has no value; interest coverage (health and risk) for a company that earns at least as much interest as it pays; `value_peg` when there is no earnings growth to divide by; `risk_debt_wall` for a company with no debt.
- `unknown`: all dividend checks when no dividend history is stored; peer checks with fewer than 5 peers; future checks with no stored estimates; price checks with no price in the statement currency; any check whose figure is not filed.
- A company with a trailing EPS that is not positive has no earnings growth, so those future checks are `unknown` ("n/m").
- `risk_insider_selling` is unknown without stored insider filings (US Form 4 only).
- `risk_short_runway` passes with the note "Not burning cash: no runway limit applies" when the runway reason says the company is not burning cash.

## Limits

- Thresholds are StewardAI's own named rules, not a vendor scoring.
- A pass or fail says only that one figure met one test. It does not say the company is good or bad.
- The latest fiscal year is used. Filings can be months old.
- Peer comparisons depend on the peer group and scope shown on each check.
- Scores of different companies are comparable only when coverage is similar.

## Golden examples

Test file: `apps/backend/tests/test_checks.py`.

- `test_healthy_company_passes_every_scored_check`: every scored axis has 6 passes of 6 applicable checks, score 6.00.
- `test_each_axis_has_six_checks`: each scored axis has exactly six checks.
- `test_fail_states_and_sentences_use_the_figures`: P/E 30 against a peer median of 20 (8 peers) fails, with the sentence "P/E (30.0x) is above the peer median (20.0x, global, 8 peers)".
- `test_peer_median_needs_enough_peers`: 3 peers is unknown and the sentence says "needs 5".
- `test_loss_maker_uses_ev_to_ebit_then_sales`: with no positive P/E or EV/EBIT, EV/sales 2 against a peer median of 3 passes.
- `test_non_payer_dividend_axis_is_not_applicable_not_zero`: all six dividend checks are `na`, no score.
- `test_bank_balance_sheet_checks_are_not_applicable`: the bank checks listed above are `na`.
- `test_debt_to_equity_passes_when_falling`: 0.6 is above 0.4 but down from 1.0 five years earlier, so pass.
- `test_yield_below_floor_is_not_applicable`: yield 0.4 % is below the 1 % floor, so `na`.
- `test_score_never_counts_na_or_unknown`: for 30 random state mixes, applicable equals passes plus fails and score is `6 * passes / (passes + fails)` to 2 decimals, or none.

Test file: `apps/backend/tests/test_insight_golden.py`, `test_past_axis_scores_passes_over_applicable_checks_only`.

- 4 passes, 1 fail, 1 not applicable: score 6 x 4 / 5 = 4.80, coverage 5 of 5 relevant = 1.
- 2 passes, 1 fail, 3 unknown: score 6 x 2 / 3 = 4.00, coverage 3 of 6 relevant = 0.5.

## Version

Method `checks` version 1.0.0 (`apps/backend/src/steward/analytics/versions.py`).
