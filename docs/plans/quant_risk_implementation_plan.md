# StewardAI: quantitative finance and risk management implementation plan

Research snapshot: 5 October 2026. **Sequencing (revised 5 October 2026):** the owner's strategy now puts quant and risk figures inside every decision (principles 5–9), so `Q0`, `Q1`, the Expected Shortfall part of `Q2` (`Q2.1`) and the decision-loop phase `Q8` run **alongside the early phases** of the [Morningstar-style analytics plan](morningstar_features_implementation_plan.md); `Q8` starts on today's per-stock risk data and switches to the covariance model when `Q1` lands. The rest of this plan follows after the Morningstar plan. Re-check the "What exists today" table against the code before starting, since that plan touches the same risk, performance and scenario areas. Inputs: the owner's strategy (`strategy/strategy.json`, principles 1–11 and the `risk` block), the existing risk code (`steward/portfolio/risk.py`, `steward/portfolio/risk_limits.py`, `steward/reference/risk.py`) and the [Morningstar-style analytics plan](morningstar_features_implementation_plan.md), whose Phase 6 items `M6.1`, `M6.2` and `M6.5` this plan implements in more depth. Where the two overlap, this plan's tasks replace them.

## Goal and the line this plan does not cross

The owner's strategy is an event- and fundamentals-driven rotation with an ETF core (principles 2 and 6): profits are taken when a thesis has played out and reinvested in the opportunity with the highest **risk-adjusted** expected return, switching only when the gain clears the cost of switching (principles 7c, 9e and 9f). Quant and risk figures (volatility, downside, correlation, contribution to portfolio risk) are part of the fundamentals of every decision. "A price fall alone is never a reason to sell", and a risk-limit breach on its own only forces a thesis review (principle 4). The horizon rolls forward; capital preservation still matters.

So quant finance here does **not** mean price signals, timing or automated trading. It does four jobs:

0. **Price every decision for risk**: the expected return and risk profile of every holding and bench idea, the risk-adjusted ranking, the switching rule and risk-based sizing (phase `Q8`).

1. **Measure risk before it happens** (ex-ante), not only after (realized). Answer: "what is this portfolio's risk *today*, where does it come from, and what does a proposed trade do to it?"
2. **Size and allocate** within the owner's caps: starting sizes, deposit allocation (principle 8c), tier balance — so that risk, not only weight, is diversified.
3. **Test the process honestly**: whether the risk model is calibrated, and whether thesis decisions add value against the benchmark, with the statistical noise of a small, short-horizon portfolio made visible.

Every quant output is an input to a thesis review, the decision loop, the monthly review, the pre-trade check or the deposit plan. Rankings and switch flags say what the rules point to; the owner decides and trades in Trading212. Nothing here places or blocks a trade.

## What exists today and where it falls short

| Area | Today | Gap a quant approach closes |
| --- | --- | --- |
| Portfolio volatility (limit: 20 %) | Realized stdev of the flow-adjusted TWR index over the last year (`portfolio/risk.py`) | Backward-looking: mixes positions sold months ago with today's book, and cannot evaluate a proposed trade. Needs a **forecast** from current weights × covariance. |
| Pair correlation (limit: 0.85) | Pearson of daily local-currency returns, last year (`risk_limits.pair_correlations`) | Daily closes of a US and a European listing are non-synchronous, which biases cross-market correlation down. Returns are in local currency, not CZK, so FX co-movement is invisible. Pairwise only; a cluster of five 0.7-correlated names never trips it. |
| Drawdown (limit: 20 % from 1-year peak) | Current drawdown of the TWR index | Reports where we are, not how likely a 20 % drawdown is over the horizon. |
| Sharpe, beta | Sharpe vs CZEONIA, beta vs the first benchmark | No significance test (a Sharpe estimated over 1–2 years is mostly noise), no downside measures. |
| Risk tiers (principle 3) | Set by hand; criteria include "annualised volatility above 35 % / at most 25 %" | The volatility criterion is computable and not computed or alerted when it crosses. |
| Price history | `history_years` defaults to 2 | Too short for 2020 and 2022 stress replays or stable covariance. |
| Numerics | Pure Python, no numpy | Covariance matrices, shrinkage, regressions and Monte Carlo need a linear-algebra library. |

## Methods, chosen for this portfolio

The portfolio is small (8–25 positions), multi-currency (CZK base, USD/EUR listings), long-only, unleveraged, with a short horizon. Methods below are picked for that shape; heavier institutional machinery is listed under "Not doing, and why".

| Topic | Method | Why this one |
| --- | --- | --- |
| Return basis | Daily log returns in **CZK** (each close × that day's CZK rate), plus **weekly** (Wednesday-to-Wednesday) returns for correlation and covariance | Measures risk the way the owner experiences it. Weekly returns remove the US/EU closing-time asynchrony. |
| Covariance | EWMA on **weekly** CZK returns (half-life 26 weeks, λ ≈ 0.974) for the current regime, and Ledoit-Wolf constant-correlation shrinkage of the weekly sample covariance (3 years) for the long-run view; show both, annualised ×52 | Sample covariance with N ≈ 20 assets is unstable; shrinkage fixes that. EWMA reacts to volatility regimes; long-run anchors it. Daily EWMA is not used: it would reintroduce the US/EU closing-time bias. |
| Short-history holdings | Proxy: with ≥ 26 weeks of own history, regress on a sector/country ETF (else the first benchmark) and backfill the missing span with β × proxy + resampled own residuals (fixed seed, so the matrix stays positive semi-definite and the result reproducible); under 26 weeks the position is excluded and reported as uncovered weight | A recent IPO or new listing must still get a risk number, labelled as proxied. |
| ETFs | Use the ETF's own price series in the covariance; look-through stays for exposure and single-name concentration | Simpler and more accurate for risk than rebuilding from holdings. |
| Risk decomposition | Euler risk contributions: RC_i = w_i (Σw)_i / σ_p; aggregated by tier, sector, country and currency | Answers "where does the risk come from", additive to 100 %. Shows when a 6 % position contributes 15 % of risk. |
| Diversification | Diversification ratio (Σw_iσ_i / σ_p) and effective number of bets (Meucci, entropy of principal-component risk contributions) | "At least 8 positions" can still be one bet. These say how many independent bets the book holds. |
| Tail risk | Historical and filtered-historical-simulation VaR and Expected Shortfall (CVaR) at 95 % / 99 %, 1-day and 1-month; Cornish-Fisher VaR as cross-check | ES is coherent and tail-aware; FHS rescales history to today's EWMA volatility. |
| Drawdown risk | Stationary block bootstrap (Politis-Romano, mean block ~20 days) of the current portfolio's CZK returns over 1, 2 and 3 years: probability of breaching the 20 % drawdown limit, distribution of max drawdown and terminal value; Conditional Drawdown at Risk | Matches the owner's real question: "how likely is a drawdown I can't recover from inside 2–3 years?" Block bootstrap keeps volatility clustering. |
| Stress testing | Historical replay on **current weights**: GFC (Sep 2008–Mar 2009), euro crisis (Jul–Sep 2011), Covid crash (Feb–Mar 2020), 2022 rate shock, plus CZK appreciation episodes; hypothetical factor shocks (equities −20 %, USD/CZK −10 %, rates +100 bp, sector −30 %) | Turns tail risk into named, explainable events. Uses proxies (above) where a holding did not exist. |
| Factor exposures | Time-series regression of each holding and the portfolio on Fama-French 5 factors + momentum (Ken French Data Library, Developed / Europe / US, free daily and weekly CSVs), plus currency (USD/CZK, EUR/CZK) and a rates factor (change in 10-year yield) | Reveals hidden concentration (e.g. every "growth" holding is the same long-momentum, short-value bet) that sector and country caps miss. Feeds principle 7c. |
| Position sizing | Risk-aware starting size: min(tier start cap from principle 8b, size where the position's marginal contribution to ex-ante volatility stays within its tier's risk budget). Fractional Kelly (¼) from the thesis's expected return only as an **upper bound** shown alongside, never as a target | Keeps the owner's caps authoritative; sizing by risk makes a 1.5 % speculative position and a 5 % core position comparably risky. |
| Deposit allocation (principle 8c) | Small constrained optimizer: allocate a deposit to minimise the distance to tier/sector/country under-representation, subject to all caps, no buy above cap, a ceiling on ex-ante volatility, and whole-share rounding; ranked candidate list as output | Implements principle 8c mechanically and shows the risk impact of each choice. |
| Expected returns (only where needed) | From the owner's thesis and the valuation module (implied return to fair value over the horizon), shrunk toward the benchmark's equilibrium return (Black-Litterman style, with confidence from thesis status) | The only expected returns that fit a fundamental strategy. Used for Kelly bounds and deposit ranking, never for timing. |
| Performance evaluation | Brinson-Fachler attribution vs the benchmark by sector and tier; factor attribution (alpha vs factor returns); Probabilistic Sharpe Ratio and Minimum Track Record Length (Bailey & López de Prado); Sortino, Calmar, Ulcer index, tracking error, information ratio | Separates skill from factor exposure and from luck, and says how long a track record needs to be before a Sharpe ratio means anything. |
| Model validation | VaR backtest: Kupiec proportion-of-failures and Christoffersen independence tests; volatility forecast vs realized (bias and Mincer-Zarnowitz regression) | A risk number with no calibration record is not trusted. |

### Not doing, and why

- **Mean-variance optimisation of the whole portfolio.** Expected-return error dominates; the owner's book is chosen by thesis, not by an optimizer. The deposit optimizer is deliberately small and constrained.
- **Trend, momentum or volatility-timing signals.** Principle 6 lets price matter only through the expected return it implies and a loss with a fundamental miss, not through trend. Volatility regime appears only as context in reviews.
- **Hierarchical risk parity / risk parity as a target.** Useful for systematic books; here, risk contributions are diagnostic, and tier risk budgets are the only "parity" idea used.
- **Option-based hedging, leverage, shorting.** Excluded by principle 2d.
- **GARCH-family models.** EWMA is close in accuracy for portfolio-level forecasting at this size and far easier to validate; revisit only if the VaR backtest fails.

## Engineering rules

These follow the rules in the Morningstar plan and the existing risk code:

1. Every figure is computed in backend code from stored observations. The LLM explains a computed figure and cites it; it never computes one.
2. Every output carries `value`, `as_of`, `window`, `observations`, `method_version`, `coverage` (weight covered by real vs proxied vs missing history) and `warnings`. Too few observations returns `null` with a reason, as `portfolio/risk.py` does today.
3. Methods are documented in the module docstring with formula and references, like `portfolio/risk.py`.
4. Golden tests: each estimator is checked against hand-computed or published reference values (e.g. Ledoit-Wolf against the reference implementation's output on a fixed matrix; Kupiec against textbook examples).
5. Results that feed a decision (monthly review, pre-trade check) are snapshotted with their `method_version`, so a later model change never rewrites what the owner saw.

## Owner decisions (accepted 5 October 2026)

The owner accepted every recommendation below. Implementation notes from the design pass:

- D1: add `numpy` in Q0; add `scipy` only in Q5, when the deposit optimizer needs it.
- D2: changing the `PriceSettings.history_years` default does not change a stored jobs config that already pins `2`; set it to 10 in the Jobs editor as part of Q0.3.
- D3: the forecast side of the 20 % limit tests the **higher** of the EWMA and Ledoit-Wolf forecasts (conservative, matching the capital-preservation goal), on total-value weights (cash counted at zero volatility); a separate finding key (`risk:volatility_forecast`) keeps it apart from the realized one.
- D6: the holding's volatility for the tier check is the same one-year daily local-currency figure the holding page already shows (`reference/risk.volatility`), so the alert matches what the owner sees.
- D4/D5 limits become new optional `RiskLimits` fields (null = off). When each is implemented (effective bets and tier budgets in Q1.3, ES and drawdown probability in Q2.4), add it to `strategy/strategy.json` and `strategy/strategy-import.json` with the value below, and extend principle 4 in the strategy notes to name it. Nothing has been changed in the strategy files yet.

| # | Decision | Accepted |
| --- | --- | --- |
| D1 | Add `numpy` and `scipy` to the backend | **Yes**: numpy in Q0, scipy in Q5. Pure-Python covariance and Monte Carlo would be slow and harder to verify. |
| D2 | Price `history_years` for holdings, benchmarks and proxy ETFs | **10 years** (from 2). Covers 2020, 2022, the April 2017 end of the ČNB's CZK floor and the late-2018 sell-off; 2008/2011 replays still come from proxy ETFs/indices only. |
| D3 | Which volatility the 20 % `max_volatility` limit tests | **Both**: ex-ante forecast (higher of EWMA and Ledoit-Wolf) as the primary, realized as before. A breach of either forces a thesis review, never a sale. |
| D4 | Tier risk budgets (share of ex-ante volatility per tier) | **Core ≤ 45 %, growth ≤ 45 %, speculative ≤ 25 %** of risk; review the values after the first month of data. |
| D5 | New review-trigger limits, severity 2 | **1-month 95 % ES ≤ 12 %; P(drawdown ≥ 20 % within 2 years) ≤ 25 %; effective number of bets ≥ 4.** Recalibrate after Q2 has produced a month of results. |
| D6 | Automate the volatility criterion of the risk tiers (principle 3) | **Compute and flag only**: a holding above 35 % volatility not tiered speculative, or a core holding above 25 %, raises a review; the owner reclassifies by hand. |

## Phase Q0 — Foundations: return matrix and data depth

**Outcome:** one tested function that returns an aligned CZK return matrix for the current book, the input to every later phase.

- [ ] `Q0.1` Add `numpy` and `scipy` (D1). New package `steward/quant/` for pure calculation modules, no I/O, mirroring `reference/risk.py`.
- [ ] `Q0.2` `quant/returns.py`: build daily and weekly CZK return matrices for a set of series (holdings, ETFs, benchmark, proxies) from stored closes and stored FX history; handle exchange holidays (carry-forward close, zero return), corporate actions via the existing adjusted series, and report per-column coverage.
- [ ] `Q0.3` Raise `history_years` per D2 and backfill; add proxy ETF series (sector and region ETFs listed in `jobs` config) so every holding has a proxy candidate.
- [ ] `Q0.4` Proxy backfill for short histories (method table above), with the proxy, fit window and R² stored and shown.
- [ ] `Q0.5` Fix the existing pair-correlation limit to use weekly CZK returns from Q0.2 (keeps the 0.85 threshold, now measured correctly).
- [ ] `Q0.6` Golden tests for alignment, FX conversion and proxy backfill on fixtures with known answers.

**Exit check:** for the live portfolio, the matrix covers ≥ 95 % of invested weight with real or proxied history, and a hand-checked holding's CZK return over a month matches its Trading212 return within FX rounding.

## Phase Q1 — Ex-ante risk model and decomposition

**Outcome:** "today's risk" from current weights, broken down by where it comes from.

- [ ] `Q1.1` `quant/covariance.py`: EWMA and Ledoit-Wolf covariance; portfolio volatility forecast for both.
- [ ] `Q1.2` `quant/decompose.py`: Euler risk contributions per position, aggregated by tier, sector, country and currency; marginal contribution to risk; diversification ratio; effective number of bets.
- [ ] `Q1.3` Extend `risk_limits.evaluate_risk` with ex-ante volatility (D3), tier risk budgets (D4) and effective bets (D5); breaches go through the existing breach and alert path at the configured severity.
- [ ] `Q1.4` Tier criterion check (D6): flag holdings whose annualised volatility crosses 25 % or 35 %, with the window used.
- [ ] `Q1.5` API `GET /portfolio/risk/ex-ante` and frontend: risk-contribution bar (weight vs risk share side by side), tier/sector/currency risk breakdown, ex-ante vs realized volatility, coverage line naming proxied positions.

**Exit check:** risk contributions sum to 100 % of forecast volatility; an EWMA forecast matches a reference implementation on a fixed fixture to 1e-9.

### Status of Q0 and Q1 (implemented 9 October 2026) and the gaps that remain

Code: `steward/quant/{returns,proxy,covariance,decompose}.py`, `steward/portfolio/exante.py`, new `RiskLimits` fields, `GET /portfolio/risk/ex-ante`, the web tab `/risk/ex-ante`. Tests: `tests/test_quant.py`, `tests/test_exante.py`, `lib/risk/ex-ante.test.ts`. Tasks `Q0.1`–`Q0.6` and `Q1.1`–`Q1.5` are built; the items below are what is not done, not verified, or deliberately different from the text above. Close them before Q2 relies on this layer.

**Not verified (exit checks and runtime)**

- G1. Q0 exit check not run: no one has confirmed that ≥ 95 % of the live book's invested weight gets real or proxied history, or that a hand-checked holding's CZK return matches Trading212 within FX rounding.
- G2. Nothing ran against a database. `load_book`, `_book_from_snapshot`, `book_from_positions` and `gather_risk_inputs` with the new limits are untested; the tests use a synthetic `LoadedBook`. Needs a `db`-marked test that runs a sync and checks the findings open, alert once and clear.
- G3. The web page was typechecked, linted and its helpers unit-tested, but never rendered: no screenshot, no mobile-width check, no keyboard or contrast check, no component test.
- G4. No measurement of cost. Each API call and each sync loads up to ~3 years of daily closes for every holding, the benchmark and 13 proxy ETFs and builds the matrix with per-day FX lookups, uncached. Time it on the live book; cache per sync if slow.
- G5. The Ledoit-Wolf "golden test against the reference implementation" (engineering rule 4) is a test against a line-by-line transcription of the authors' published `covCor` formulas, not against their code or a library output. The EWMA test is likewise against a loop written for the test. Add a fixture with output from a published implementation. **Closed:** `tests/fixtures/ledoit_wolf_cc.json` holds PyPortfolioOpt 1.6.0 output (constant correlation); our `ledoit_wolf` agrees to 1e-9 (`test_ledoit_wolf_matches_pyportfolioopt_output`).

**Data gaps**

- G6. Proxy ETFs have no stored prices until the prices job next runs with the new `proxy_series`; until then short-history holdings show as left out. Not yet confirmed that Stooq has all 13 default symbols (`xlk.us` … `vt.us`) or that the Yahoo fallback covers them.
- G7. `history_years` defaulting to 10 does not change a stored jobs config that pins 2 (D2). Setting it to 10 in the Jobs editor is a manual step still to be done; FX re-backfills from Frankfurter when it grows.
- G8. FX for the return matrix is hard-wired to ECB reference rates (the only source backfilled over `history_years`), while the app's base-currency figures use ČNB for a CZK base. Differences are small but the two are not identical; ČNB history is not backfilled anywhere.
- G9. Default proxies are US sector ETFs plus Europe, emerging markets and world. A European or Czech small cap has no sector-matched proxy; the choice is purely highest R² among the candidates and the benchmark, with no sector or country mapping from the holding.
- G10. Q0.4 asked for the proxy, fit window and R² to be "stored and shown". They are shown and returned by the API but not persisted; they are recomputed on each request and can change as data arrives.
- G11. A series whose last close is more than 7 days old has no level, so its recent weeks look missing and are silently backfilled from the proxy and labelled "proxied", which can hide a stalled price feed. Add a stale-feed warning.
- G12. Daily CZK returns (Q0.2) are built by `build_return_matrix(..., "daily", ...)` and tested, but nothing loads or uses them; `load_book` builds weekly only. Q2 (tail and bootstrap) needs a daily loader and proxy handling for daily data (the backfill is weekly only).
- G13. The final partial week (after the last Wednesday) is not in the matrix, so the model lags the latest prices by up to six days.

**Method differences and open decisions**

- G14. The pair-correlation limit now uses 2 years of weekly returns with at least 40 shared weeks. The plan kept the 0.85 threshold but did not set a window, and it dropped the old 1-year daily measure. The 0.85 level was chosen for daily data; weekly correlations are typically higher between US and European names, so the threshold may need recalibrating. Principle 4(d) in the strategy notes still says "return correlation" without the window.
- G15. Positions left out of the model count as zero risk, so the forecast understates the risk by about their weight (reported in `coverage`, with a warning above 0.5 %). Decide whether the limit should instead be tested on a scaled-up forecast.
- G16. Weights are shares of total portfolio value including cash (D3) regardless of the strategy's `weight_basis`. A strategy set to `invested` weights will see tier and position weights here on a different basis from the rest of the app.
- G17. EWMA is zero-mean (RiskMetrics) and both models share one 156-week window; the plan's "Ledoit-Wolf on 3 years, EWMA for the current regime" is satisfied but the EWMA window is not independently chosen.
- G18. Backfilled weeks resample the regression residuals, so risk numbers for a proxied holding are not fully reproducible if the proxy list, window or data change; the draw is seeded per series key only.
- G19. The look-through of ETFs into their underlying companies is not used for risk (the plan says to use the ETF's own series; correct), so risk by sector and country is by the holding's classification label, not the X-Ray's look-through exposure. An ETF classified "Unclassified" sits in its own bucket.

**Limits and strategy**

- G20. Tier-based features are inert until holdings carry a `risk` classification: `strategy.json` has an empty `classifications`, so tier budgets and tier-criteria checks have nothing to act on, and unclassified holdings are never flagged. Classify the holdings (or add an import path).
- G21. The new limits take effect only after the updated strategy is imported; the files are updated but not loaded into the app.
- G22. D5 limits for expected shortfall and drawdown probability are not added (they belong to Q2.4). Principle 4(e), a per-stock contribution cap of 8 %, is Q8.6 and has no check; the risk shares computed here are its input.
- G23. Tier-criteria checks cover only the volatility criterion (D6). Market cap, profitability history and the other principle 3 criteria are not checked.
- G24. The tier flag is one finding per ticker and rule, so a holding that breaches both rules opens two alerts.

**Engineering rules not yet met**

- G25. Rule 2 (every output carries value, as_of, window, observations, method_version, coverage, warnings): the report has these at the top level and per position, but the individual figures (forecast volatility, risk shares, tier checks) do not each carry their own window and observation count, and "too few observations returns null" applies only at the whole-model level.
- G26. Rule 5 (results that feed a decision are snapshotted with `method_version`): not done. Findings carry the observed value but the report itself is recomputed live, so a later model change rewrites what the owner saw. Becomes necessary at Q2.4 / Q5.1.
- G27. Method notes are in module docstrings; the README, the setup guide and the glossary (Q7.4) have no entry for the new measures, and the page's wording for "risk share", "independent bets" and "diversification ratio" has not been reviewed with the owner.

**Not in scope of Q0/Q1 but touched by them**

- G28. Q1.5 listed ex-ante vs realized volatility and a coverage line on the page; the Overview risk tile and the holding pages show neither the forecast nor a position's risk share. The holding page's volatility figure and the new tier check use different windows and bases (daily local currency vs weekly CZK).
- G29. No chat tool or monthly-review section uses the new figures (Q5.6, Q7.1, Q7.2); Q8's stage B switch (from standalone volatility to marginal risk) is therefore still open, even though the marginal contribution to risk is now computed and returned per position.

## Phase Q2 — Tail and drawdown risk

**Outcome:** the probability of the outcome the strategy fears most.

- [x] `Q2.1` `quant/tail.py`: historical, filtered-historical and Cornish-Fisher VaR and ES at 95/99 %, 1-week and 1-month horizons (weekly returns, not 1-day), per portfolio with each position's ES contribution (`portfolio/tail.py`, `GET /portfolio/risk/tail`).
- [x] `Q2.2` `quant/bootstrap.py`: stationary block bootstrap of the current book over 1, 2 and 3 years (10 000 paths, fixed seed per run so a result is reproducible); outputs P(max drawdown ≥ 20 %), max-drawdown quantiles, terminal value quantiles, Conditional Drawdown at Risk, median time to recover a 20 % fall.
- [x] `Q2.3` Downside statistics on realized history: Sortino, Calmar, Ulcer index, downside deviation, skew and kurtosis — completes `M6.1`. (`quant/downside.py`.)
- [~] `Q2.4` New limits `risk.max_es_95_month` and `risk.max_drawdown_probability` (with `drawdown_probability_years`) are evaluated at every sync (findings `risk:es` and `risk:drawdown_probability`). Each sync saves the report as a `tail` snapshot with its `method_version` (G101 closed).
- [~] `Q2.5` Frontend: the page Risk → "Bad months" has the one-month loss sentence in CZK, the loss table by method, the drawdown odds for 1, 2 and 3 years, who carries the tail and the downside statistics. Not built: the drawdown fan chart (G102).

**Exit check:** on a synthetic normal i.i.d. series, bootstrap and closed-form VaR agree within sampling error; the drawdown probability is stable (± 1 pp) across seeds.

## Phase Q3 — Stress tests and scenarios

**Outcome:** named events in CZK on today's portfolio, completing `M6.2` and the risk side of `M6.5`.

- [x] `Q3.1` `quant/stress.py`: historical replay of the episodes in the method table on current weights, using real series where they exist and proxies otherwise; result per position and in total, with proxied share shown.
- [ ] `Q3.2` Hypothetical shocks through the factor betas of Q4 (equity, USD/CZK, EUR/CZK, rates, sector); owner-set sensitivities allowed where history is inadequate, labelled as owner assumptions.
- [ ] `Q3.3` Saved scenarios with their assumptions; each scenario run is snapshotted.
- [x] `Q3.4` Frontend stress table: scenario, portfolio loss, worst three contributors, whether the 20 % drawdown limit would be hit.

**Exit check:** shocked position values sum to the shocked portfolio total; every proxied loss is labelled.

## Phase Q4 — Factor exposures

**Outcome:** hidden common bets made visible.

- [x] `Q4.1` Ingest the Ken French Data Library factor files (Developed, Europe, US: 5 factors + momentum, daily and weekly) as a macro source with the existing source-status and coverage tracking.
- [x] `Q4.2` `quant/factors.py`: rolling (2-year weekly) regressions per holding and for the portfolio, with t-statistics, R² and Newey-West standard errors; currency and rates factors added.
- [x] `Q4.3` Portfolio factor tilt vs the benchmark's tilt; flag a tilt beyond ±0.3 as a concentration note in the monthly review (not a breach).
- [x] `Q4.4` Use factor exposures in "propose new ideas" (principle 7c): rank candidates partly by how much they reduce the dominant factor tilt.
- [x] `Q4.5` Frontend: factor exposure chart for portfolio vs benchmark, per-holding table.

**Exit check:** the benchmark ETF regressed on its own region's market factor gives beta ≈ 1 and R² > 0.9.

### Status of Q3 and Q4 (implemented 9 October 2026) and the gaps that remain

Code: `quant/{stress,factors,french,french_store}.py`, `portfolio/{replay,factors}.py`, migration `20261024100000_factor_return.sql`, job `factor_returns` (Mondays 05:30), `GET /portfolio/risk/stress-replay` and `GET /portfolio/risk/factors?region=`, web tabs "Past episodes" (`/risk/replay`) and "Factors" (`/risk/factors`). Tests: `tests/test_stress.py`, `tests/test_factors.py`, `lib/risk/{replay,factors}.test.ts`. Built: `Q3.1`, `Q3.4`, `Q4.1`, `Q4.2`, `Q4.3`, `Q4.5`. Not built: `Q3.2`, `Q3.3`, `Q4.4` (below). The Q4 exit check is met on synthetic data (benchmark = market plus noise gives beta 1.0 ± 0.05 and R² > 0.9); the Q3 exit check holds by construction (`after = before + Σ position results`, tested) and every proxied position is labelled.

**Not built**

- G30. `Q3.2` hypothetical shocks through the factor betas: not started. `decision/shocks.py` (M6.2) already applies owner-stated shocks with measured market betas; routing equity, currency and rates shocks through the Q4 loadings is the change, and needs the rates factor (G34).
- G31. `Q3.3` saved scenarios and snapshots: not started. The replay report is computed live and not stored (engineering rule 5, same as G26).
- G32. `Q4.4` factor-aware idea ranking (principle 7c): done as `portfolio/idea_fit.py`, `GET /portfolio/risk/factors/ideas` and the `idea_factor_fit` chat tool (idea added to a copy of the book at a stated weight, factor regressions rerun, relief = drop in distance of style tilts from the benchmark).

**Not verified**

- G33. Nothing ran against a database or live prices. The factor job, `load_factors`, `factor_exposures` and `stress_replay` are tested only on synthetic data; the six Data Library URLs were checked by downloading each file and parsing it with the production parser (all six parse; the files end on 31 Aug 2026), but the job itself has not run. The pages were typechecked and linted but not rendered or checked visually, at mobile width or by keyboard.

**Data and method gaps**

- G34. No rates factor: Q4.2 asked for a change-in-10-year-yield factor and the report has only the six style factors plus USD and EUR against the base currency. Needs a stored 10-year yield series.
- G35. Stored price history starts where `history_years` reaches (10 years from the first price run), so the 2008 and 2011 episodes come back `no_data` and the 2017 CZK-floor episode needs a base-currency series from April 2017. ECB FX is backfilled only over `history_years` as well (G8). To replay 2008 and 2011, give the proxy ETFs, the benchmark and FX a longer history than the holdings (a separate setting).
- G36. A proxied replay uses a weekly-return beta on a proxy applied to a peak-to-trough move, with alpha ignored. A beta fitted on calm weeks understates a crash (betas rise in stress), so proxied losses are probably too small; each is labelled, but not flagged as likely understated.
- G37. Episode dates are approximate (peak and trough chosen by hand) and fixed in code; the owner cannot add an episode.
- G38. The Data Library publishes in US dollars and a month or more late (the stored files end 31 Aug 2026 on 9 Oct 2026), so the latest weeks are left out of the regressions (reported as `weeks_without_factors`). Regressing base-currency returns on dollar factors leans on the USD and EUR terms to absorb the currency part; a holding in a third currency (GBP, CHF) has that part left in its residual.
- G39. One region is used for the whole book and every holding; the page lets the owner switch. A per-holding region (European names on the Europe factors) is not chosen automatically, and Q4.3 compares the portfolio with the first benchmark only.
- G40. The risk-free rate is the US one for every currency; the difference is small at weekly frequency but not zero for CZK.
- G41. The tilt note appears on the page. The monthly review does not include it yet (Q7.1), and the factor table has no coverage tracking beyond first and last date in the response (no source-status row in the existing macro status screens, since the data is stored outside the macro tables).
- G42. Per-holding regressions use the holding's own weeks only, so a holding with 26 to 39 weeks has no fit (`MIN_OBSERVATIONS` = 40); the portfolio line uses backfilled weeks, so a short-history holding contributes proxy loadings to it (labelled in the warnings).

### Gap closure (9 October 2026)

**Closed in code:** G4, G9, G10, G11, G12, G14, G15, G16, G17, G18, G24, G26, G27 (glossary), G28, G29 (chat tools `risk_forecast`, `past_episodes`, `factor_tilts`), G30 (factor-shock tool on the Factors page), G31 (saved scenarios, append-only report snapshots with `method_version`), G34 (US 10-year yield factor and extra held-currency factors), G35, G36, G37 (owner episodes in `risk.stress_episodes`), G38, G41 (a "Risk" section in the monthly review), G42. G2 is covered by `tests/test_quant_db.py` (real database).

**G20, changed:** the strategy names no holding. `risk.auto_tiers` plus the thresholds make the backend work out each holding's tier from its own numbers (volatility, market cap, profit; a fund by volatility alone), store it in `holding_risk_tier` and use it wherever a page or limit groups by tier. An owner entry in `classifications` still wins. Profit is judged on the current margin, not five years of history (G23 stays open).

**Done on the live database (9 October 2026):** G7, `prices.history_years` 2 → 10 (jobs config version 59). A tier list naming holdings was saved as strategy version 60 and undone as version 61 once the rule above was set.

**Not done, with reasons**

- G1, G33, partly done: the four quant migrations are applied to the live database, `factor_returns` ran there (144,319 rows, all three regions, 2000 to 31 August 2026) and `macro_collect` ran after `us_10y` was added to the jobs config (version 62). `price_history` was interrupted twice (a network drop, then a stop for low memory) and must be run again to fetch the 10- and 20-year histories. Still to do after that: run the ex-ante, replay and factor reports on the live book, a portfolio sync from the deployed branch, and compare one holding's CZK return with Trading212.
- G3: pages were not rendered; check them in a browser at phone width and by keyboard.
- G13: a final partial week has a different length from the others, so it stays out.
- G32, partly: the ranking needs stored daily prices for each candidate; a symbol without them is listed as "no_prices". The web page has no panel for it yet (API and chat tool only).
- G8, G19, G22, G23, G25, G39, G40: accepted as stated; revisit with Q2 and Q8.
- G21: `auto_tiers` and the other new limits take effect after the updated strategy is imported, which should happen after the deploy (the running backend would reject the unknown key).

## Phase Q5 — Sizing, deposits and the pre-trade check

**Outcome:** the decisions the owner already makes, with risk numbers attached before acting in Trading212.

- [x] `Q5.1` Pre-trade "what-if" endpoint: given proposed buys/sells or a deposit, return the full rule and risk-limit evaluation after the trade (caps, tiers, ex-ante vol, ES, drawdown probability, correlation, risk budgets, effective bets) as a before/after diff.
- [x] `Q5.2` Risk-aware starting size for a new position (method table), shown next to the principle 8b cap.
- [x] `Q5.3` Expected-return input from thesis and valuation, shrunk toward equilibrium; ¼-Kelly upper bound displayed with its inputs.
- [x] `Q5.4` Deposit allocator (principle 8c): ranked allocation of a given CZK amount, whole shares, all constraints, risk impact of each line; read-only.
- [x] `Q5.5` Rule 9 helper: for a position above 10 %, show the dilution path from expected deposits and the trim size to 8 % when the thesis is weakened.
- [x] `Q5.6` Chat tools: "what does buying X do to my risk?", "where should my next deposit go?" — the assistant calls Q5.1/Q5.4 and cites the computed result.

**Exit check:** an allocation never breaches a cap in the post-trade evaluation; every proposal cites the rule or KPI it serves (as the monthly review already requires).

### Status of Q5 (implemented 9 October 2026) and the gaps that remain

Code: `quant/sizing.py` (equilibrium and shrunk returns, quarter-Kelly, bisection, dilution path, trim), `portfolio/trade_risk.py` (Q5.1), `portfolio/sizing.py` (Q5.2, Q5.3), `decision/allocator.py` (Q5.4), `portfolio/concentration.py` (Q5.5), chat tools `risk_whatif`, `start_size`, `deposit_allocation`, `over_cap_positions` (Q5.6). Endpoints: `POST /portfolio/pretrade` now returns `risk` (before and after), `GET /portfolio/risk/{sizing,deposit,over-cap}`. Web: "Your risk before and after" on `/risk/pretrade`, the new tab `/risk/deposit` ("Size and deposit"), and the idea panel on the Factors page (Q4.4). Tests: `test_sizing.py`, `test_trade_risk.py`, `test_allocator.py`, `test_concentration.py`, `test_sizing_db.py`, `lib/risk/sizing.test.ts`.

- `Q5.1`: the trades and deposit are applied to a copy of the loaded book and the ex-ante report is rebuilt, so before and after share the model, proxies and tiers. The ex-ante limits (volatility forecast, tier risk budgets, effective bets, pair correlation, tier criteria) are evaluated on both sides with the code that raises them after a sync; each is marked new, deeper, still open, eased or cleared, and added to the pre-trade checks. The rule, cap and band evaluation was already there.
- `Q5.2`: suggested = min(tier starting cap of 8b, largest weight at which buying from cash adds no new or deeper ex-ante breach and keeps the stock's own share of volatility within `risk_contribution_cap` when set). The tier cap stays authoritative.
- `Q5.3`: Kelly bound = ¼ × (expected return − cash rate) ÷ variance, with the expected return the thesis return to its sell target annualised, pulled toward CAPM equilibrium (cash rate + beta × 5 % premium) by a confidence from the thesis conviction (1 to 5 maps to 20 to 80 %, none stored 50 %). Shown with all its inputs; absent, with the reason, when there is no thesis.
- `Q5.4`: greedy by principle 8c (core below target, then the most under-represented tier, sector or country, then the best idea above the hurdle, else a core fund); each purchase is halved until the post-purchase rules, caps, bands and ex-ante limits show nothing new or deeper than leaving the deposit in cash.
- `Q5.5`: positions above the cap show the months until deposits alone dilute them, the trim to 8 % (to 10 % above the 15 % backstop) in money and shares; the outlook judgement is left to the owner.

**Gaps**

- G43. Expected Shortfall and the drawdown probability are not in the before and after, because Q2 is not built; the result names them as not modelled.
- G44. The equity premium (5 %) and the conviction-to-confidence mapping are fixed in code; the premium can be changed per request on the sizing endpoint. They should become strategy settings if the owner wants to tune them.
- G45. The deposit allocator is greedy, not a global optimiser (scipy is not added). It can miss a better combination; it is deterministic and every step is explained. Rank within a stage uses the thesis return to the sell target, not the risk-adjusted score of Q8.4.
- G46. Bench ideas are the stocks with a thesis, not held, and a stored price series and instrument record; one without them is listed under "left out" with the reason. Prices are the last stored close at the stored rate, not a live quote.
- G47. A fund is recognised by the instrument type `ETF`. A core stock that passes principle 8a is eligible for the "core" stage only through its derived or entered tier and its thesis.
- G48. Expected deposits for Q5.5 default to the last twelve months' average of `DEPOSIT` cash movements; there is no plan of future deposits.
- G49. The pre-trade check runs the ex-ante model for every request (a few seconds on a large book); nothing is cached beyond the one-minute book cache.
- G50. The pages were typechecked and unit-tested but, as before (G3), not rendered in a browser (phone width, keyboard, contrast). There are no component tests for the new panels, only for the helpers in `lib/risk/sizing.ts`.

**Not verified**

- G51. Nothing in Q5 ran against the live database or a live book. The database tests use a synthetic two-stock book; the real book (many currencies, proxied and excluded holdings, ETFs, GBX quotes) is untested. The Q5 exit check ("an allocation never breaches a cap in the post-trade evaluation") is tested on synthetic books only.
- G52. The new routes (`/portfolio/risk/{sizing,deposit,over-cap}`) and the four chat tool handlers have no test of their own; the underlying functions and the tool declarations are tested. Ticker resolution through `resolve_tickers` in the routes and tools is not tested.
- G53. The full backend suite has still not been run to completion; only the files touching this work were.

**Method gaps**

- G54. The deposit allocator ignores the cash floor and cash target (principle 2c: cash 5 %, above 10 % for 3 months is put to work): it spends as much of the amount as it can place. Sale proceeds, tax and trading or FX costs are not modelled; a deposit is the only input. No minimum order size or lot size is applied.
- G55. Rule and band evaluation in the allocator uses the strategy's classification dimensions and target bands only. The sector and country caps that the X-Ray enforces through look-through (principles 2c and 4c) are not part of the per-purchase test; a purchase can pass here and still raise the look-through finding. The pre-trade check does include the look-through limit, the allocator does not.
- G56. The risk-aware size (Q5.2) tests the whole book for new or deeper ex-ante breaches, so a standing breach elsewhere does not block a new position unless the position makes it worse; it also does not cap by event risk (binary events, principle 8b), which is `Q8.6`.
- G57. In Q5.2 the "per-stock share of volatility" cap is only the optional `pretrade.risk_contribution_cap`; the strategy's 8 % rule (principle 4e) is not yet a limit evaluated after each sync (`Q8.6`), so the size does not use it unless the owner sets that field.
- G58. Beta in the Kelly inputs is a weekly regression on the first benchmark over the stock's own weeks (at least 40); a short-history stock falls back to beta 1 with a note. The volatility is the stock's standalone volatility from the ex-ante model (EWMA or Ledoit-Wolf, whichever the forecast chose), not the 1-year realised figure the older pre-trade check uses, so the two checks can quote slightly different volatilities.
- G59. The tier of a candidate not held is derived on the fly (never stored) and only when `auto_tiers` is on; with it off and no classification the stock has no tier, and sizing says so instead of guessing.
- G60. A new stock's history comes from stored daily prices only (about 4 years); one with less than 26 weeks is left out of the model like a holding would be, and the before and after then understate its risk (the warning says so).
- G61. Q5.5 assumes no price moves while deposits dilute a position and that all deposits go elsewhere; a rising stock dilutes more slowly than shown. It reads the cap from the strictest all-stock position rule and does not see per-ticker rules, the look-through cap or the exempt-fund list in `max-single-position` (`Q8.0`); funds are exempt by instrument type only.
- G62. The pre-trade `risk` block degrades to "unavailable" with the error text if the ex-ante model fails; the rule checks still come back. The failure is logged, not alerted.
- G63. `PretradeResponse` adds a field to an existing response; clients generated before this change ignore it, but the web build must be regenerated (`npm run gen:api`) with each backend change.

## Phase Q6 — Process evaluation and model validation

**Outcome:** evidence on whether the risk model is right and whether the thesis process adds value.

- [x] `Q6.1` VaR backtest (Kupiec, Christoffersen) and volatility-forecast calibration on rolling history; results on the Activity page and in the monthly review. A failed test lowers the model's displayed confidence and opens a review item. (`quant/validation.py`, `portfolio/validation.py`, `GET /portfolio/risk/validation`, page Risk → "Does it hold up", chat tool `model_check`; the review item is the finding `risk:model_validation`.)
- [x] `Q6.2` Brinson-Fachler attribution by sector and tier vs the benchmark, monthly and since inception, reconciled to total excess return. (`quant/skill.py`, `performance/evaluation.py`, `GET /portfolio/performance/evaluation?dimension=`.)
- [x] `Q6.3` Factor attribution: alpha net of the Q4 factors, with its t-statistic. (`FactorFit.alpha_t`, `FitOut.alpha_t`, `factor_alpha` in the evaluation response.)
- [x] `Q6.4` Probabilistic Sharpe Ratio vs the benchmark's Sharpe and Minimum Track Record Length; shown as "not yet distinguishable from the benchmark" where applicable.
- [x] `Q6.5` Decision journal metrics (with `M7.1`): per closed thesis, return vs benchmark over the holding period, hit rate by tier, and whether risk-review triggers preceded losses. (`journal/process.py`, `GET /journal/process`.)

**Exit check:** Brinson effects sum to the measured excess return within 1 bp. (Tested: the reconciliation error is below 1e-6 bp on the synthetic periods, including one with a position opened inside the period.)

## Phase Q7 — Integration into reviews and alerts

- [x] `Q7.1` Monthly review: a risk section with forecast vs realized volatility, top risk contributors, tier risk budgets, ES, drawdown probability, worst stress scenario, factor tilts and model-validation status; proposed actions must cite a rule, KPI or risk limit. (Everything except ES and drawdown probability, which wait for Q2 and are stated as not modelled; an action may now cite an open `risk:` limit.)
- [x] `Q7.2` Thesis review pack: the position's risk contribution, ES contribution, correlation cluster and stress losses. (`portfolio/position_pack.py`, `GET /portfolio/risk/position?ticker=`, chat tool `position_risk`, and one line per thesis due for review in the monthly review; ES contribution waits for Q2.)
- [x] `Q7.3` Drawdown early-warning ladder ahead of principle 4g: notes at 10 % and 15 % drawdown that list the positions driving it and the theses due for re-confirmation; the 20 % freeze stays as written. (`risk.drawdown_warnings`, findings `risk:drawdown_warning:10` and `:15`, severity 1, `portfolio/drawdown_ladder.py`.)
- [x] `Q7.4` Glossary entries for every new measure, written in the owner's terms. (Eleven entries; Expected Shortfall has none because it is not built.)

### Status of Q6 and Q7 (implemented 9 October 2026) and the gaps that remain

Verified: unit tests for the Kupiec and Christoffersen statistics (a golden Kupiec value, critical values of the chi-square), the back-test on a model that holds and on one that misses a volatility jump, the PSR closed form and Minimum Track Record, Brinson reconciliation (including a deposit and a position opened inside the period), the closed-thesis pairing, the drawdown rungs and drivers, the position pack and the review's risk lines; mypy and ruff clean on the touched modules; web vitest (1074 tests), eslint and tsc clean apart from the known route-type errors.

Not verified:
- G64. Nothing here has run on the live book. The routes, the chat handlers and the sync-time review item have no HTTP or database test, and the new page has not been looked at in a browser (G3, G50 still apply).
- G65. The full backend suite was not run; only the touched modules and the related test files were.

Method gaps:
- G66. The back-test (Q6.1) holds today's weights constant over the past 156 weeks; the first 52 only seed the forecast, so at most 104 weeks are tested and the 99 % test has about one expected hit and can rarely fail. It tests the EWMA forecast, not whichever of EWMA and Ledoit-Wolf the report shows as larger. It tests the method, not the trades you made.
- G67. The displayed confidence appears on the new page and in the monthly review only; the Today's risk page does not yet carry it, and no earlier results are stored, so there is no history of the model's confidence (the saved-report table has a fixed list of kinds and was not extended). The review item `risk:model_validation` exists only when a limit that needs the book is set, and it has severity 1 whatever `on_breach` says.
- G68. The plan asked for the Activity page; the results are on Risk → "Does it hold up" instead.
- G69. Brinson (Q6.2) has one benchmark index, so every segment gets the index's return and the benchmark weights equal the portfolio's own: allocation and interaction are zero by construction and the table is each segment's contribution to the gap. A benchmark with segment returns (a sector index set) is not stored. Segments come from the strategy's classification today (a core ETF with no sector is "Unclassified"), cash is excluded, and the periods are single-period each and not chain-linked, so monthly figures do not compound to the since-inception one.
- G70. The PSR (Q6.4) uses weekly returns of the whole account with flows removed (cash included) against the benchmark's weekly price change; no risk-free rate is subtracted on either side, and the benchmark has no dividends, so its Sharpe ratio is understated. It needs at least 26 weeks. The benchmark and the portfolio are in different currencies when the benchmark is quoted abroad.
- G71. The factor alpha (Q6.3) is the intercept of the existing two-year regression on today's weights, not on the realised record; its t-statistic is Newey-West with 4 lags.
- G72. Closed theses (Q6.5) are a `sell` after a `buy` or `add` in the journal; partial trims are ignored, the first buy opens the thesis, prices are the ones written in the entries, returns exclude dividends, and the tier is today's classification. "A risk finding came first" counts findings stored in `portfolio_breach`, which only goes back to when storing began. Hit rate and the trigger share are withheld below five theses.
- G73. The monthly review (Q7.1) has no Expected Shortfall or drawdown probability (Q2 not built) and its stress line is the historical replay only. It recomputes the model check on each run. The tier budget line appears only for tiers with a budget or at least 5 % of risk.
- G74 (closed: held cards on the thesis board have a "Risk of this holding" section that loads the pack when opened). Earlier note: the position pack (Q7.2) was not on the thesis page; it is a route, a chat tool and monthly-review lines. The cluster threshold (weekly correlation 0.6) is a constant, and the ES contribution is empty.
- G75. The drawdown ladder (Q7.3): `risk.drawdown_warnings` (default 10 % and 15 %) has no field in the strategy editor; it is kept as loaded and changed through the import. The rungs apply only when `max_drawdown` is set. The drivers use today's weights from the weekly peak of the last 52 weeks, which approximates the real path and ignores trades since the peak. The note severity is fixed at 1.
- G76. The glossary entries (Q7.4) were written, not reviewed with the owner (G27 stands).
- G77. The existing drawdown test in `test_risk_limits.py` was changed: with the default rungs, a 20 % drawdown now also raises the 10 % and 15 % notes. Anyone reading the findings list for a book in drawdown will see three `risk:drawdown*` entries, and a strategy that sets `drawdown_warnings: []` gets the old behaviour.
- G78. The generated web types mark fields with a `default_factory` as optional, so the Q5 trade-risk panels needed `?? []` guards. Other new list fields (Q6, Q7) rely on the same guards in the views; a new list field added without one will fail `tsc` or render blank on an old API response.
- G79. Whether `price_history` holds the long histories the back-test and the 156-week window need is unchecked: earlier inspection found about nine rows per holding. A short history lowers the weeks tested and the confidence shown is "untested" rather than an error, so a data gap can look like a model verdict.

## Phase Q8 — The decision loop: expected return, risk-adjusted ranking and rotation

**Dependencies:** none to start (stage A uses today's per-stock risk data: daily closes, `reference/risk.py` volatility, maximum drawdown and correlation, the CZEONIA rate and the portfolio's Sharpe ratio from `portfolio/risk.py`); `Q1` and `Q2.1` upgrade it (stage B). The thesis price target from the current code (`ThesisPriceTarget`: sell and buy targets, currency, method, date) is the starting point. **Outcome:** strategy principles 5–9 and 11b computed in the app, so the owner sees every holding and bench idea ranked by risk-adjusted expected return, with switch flags, before acting in Trading212.

- [x] `Q8.0` Exempt list on the single-position rule (principle 2a): an optional `exclude: list[Ticker]` on `PositionWeightRule` (rejected when the rule names a single ticker), skipped by the `position_weight` evaluation in `portfolio/breaches.py`, with an "Except (optional)" field in the strategy editor and its form round-trip. The owner then lists the core ETFs in `max-single-position`, so they stop raising the 10 % alert while their holdings still count toward the look-through limit (principle 4c). Tests: an excluded ticker above the cap raises nothing, others still breach, a single-ticker rule with `exclude` fails validation.
- [x] `Q8.1` Thesis scenario fields (principle 5), added to `ThesisPriceTarget` and the theses editor: bear, base and bull values per share with probabilities (defaults 25 / 50 / 25 %; values must rise from bear to bull and probabilities add up to 100 %); a catalyst date; and the expected volatility band. Allow a target with scenarios only. Extend `ThesisPriceTarget.describe()` so deep research and the severe-news impact notes see the scenarios, and let deep research pre-fill drafts with citations for the owner to confirm.
- [x] `Q8.2` Expected-return module (pure functions, golden-tested): probability-weighted expected value and expected return to the current price; annualized to the catalyst date (2 years when unset, floor 3 months); base-case upside to bear-case loss ratio; flags for the return hurdle (default 30 % over the horizon) and the asymmetry gate (default 2:1) of principle 8a. Thresholds become strategy config fields (`risk` block or a new `decision` block, null = off), not constants.
- [x] `Q8.3` Risk profile per holding and bench idea (principles 5d and 9c). Stage A: 1-year and 3-month volatility, maximum drawdown, correlation with the portfolio's time-weighted index, and marginal risk approximated as correlation × volatility; the bear-case loss compared with the historical maximum drawdown. Stage B: weekly CZK returns (`Q0`), marginal contribution to portfolio risk from the covariance model (`Q1`), the stock's Expected Shortfall (`Q2.1`) and its factor exposures (`Q4`). Every figure carries its window, observation count and stage.
- [x] `Q8.4` Risk-adjusted ranking (principle 7c): `GET /theses/expected-returns` returning, for every thesis with scenarios, the price and its date, expected return (total and annualized), risk profile, risk-adjusted score ((annualized expected return − cash rate) ÷ standalone volatility in stage A, ÷ marginal risk in stage B), the "improves the portfolio" flag (risk-adjusted return above the portfolio's × correlation), hurdle and asymmetry flags, and the reason for any missing figure. A ranking table on the thesis board, holdings and bench ideas together, with the formula and inputs on drill-down. After `Q4`, an idea that adds to the dominant factor bet shows the higher expected return it needs.
- [x] `Q8.5` Switching rule (principle 9f): for each holding against the best-ranked idea, the expected-return gap over the horizon against the switching hurdle: tax on the exact lots sold (from `M4.8`; 15 % of the lot's gain when under 3 years and above the annual exemption), trading and FX costs, and the safety margin (config, default 5 percentage points). Show the suggested action (hold, partial or full switch, scaled by how far the gap clears the hurdle), the "switch now or after the time test" comparison when a lot passes 3 years within 6 months, and the risk-equivalent size of the replacement (old weight × old volatility ÷ new volatility, remainder to the next idea or the core ETFs). Run the switch through the `Q5.1` before/after evaluation; flag it only when the portfolio's risk-adjusted expected return improves with every limit held. Flags only: nothing is traded.
- [x] `Q8.6` Sizing and risk budgets (principles 4e and 8b): a per-stock cap on contribution to portfolio volatility (config, default 8 %), checked after each sync as a review-trigger finding once `Q1` exists; the suggested starting size (∝ risk-adjusted expected return ÷ volatility, ceiling only) next to the tier cap in the pre-trade check; the stock's historical moves on earnings days, so a position ahead of a binary event can be sized down.
- [x] `Q8.7` The bench (principles 7d and 8c): theses for tickers not held are the bench; give them prices (the price job fetches daily closes for bench tickers as it does for holdings), show the bench count against the 5–10 target in the monthly review, and route sale proceeds and deposits to the best bench idea above the hurdle, else the core ETFs, in the deposit allocator (`Q5.4`).
- [x] `Q8.8` Volatility-band review trigger (principle 9c): after each sync, a held stock whose 3-month realized volatility is above 1.5 × its thesis band opens a `thesis:volatility:<ticker>` finding (kind `rule`, severity 2, its own alert headline "thesis risk profile changed") through the existing breach machinery; it clears when volatility falls back inside.
- [x] `Q8.9` Switch log and scorecard (principles 9f and 11b): record each switch the owner confirms (stock sold, stock bought, date, both expected returns and risk profiles, hurdle, lots and tax); the yearly scorecard compares the bought stock with the sold one from the switch date and recommends raising the safety margin when switches do not beat holding (built with `M7.9`).
- [x] `Q8.10` Chat tools: "what should I rotate?", "where should this sale's proceeds go?", "what does switching X for Y do to my risk?"; the assistant calls `Q8.4`, `Q8.5` and `Q5.1` and cites the computed results.

**Exit checks:** golden examples reproduce the expected return, annualization and asymmetry ratio by hand (including the owner's Google example: held at 220 USD, scenarios 170 / 230 / 270 at 25 / 50 / 25 % → about +2 %; a candidate at 50 with 40 / 70 / 90 → +35 %; hurdle about 10 points → full switch at about 5 % risk-equivalent size for a 28 % → 45 % volatility change); a ranking row with a missing input says why instead of showing a number; a switch flag never appears when the post-trade evaluation breaks a limit; stage A and stage B figures are labelled as such.

### Status of Q8 (implemented 10 October 2026) and the gaps that remain

Built: the exempt list on the single-position rule (`exclude`, also honoured by the over-cap report); thesis scenarios, catalyst date and volatility band; `decision/expected_return.py` (golden-tested on the owner's Google example: +2.3 % held, +35 % candidate, 2:1 asymmetry); the stage A risk profile and the ranking (`GET /theses/expected-returns`); the switching scan with the pre-trade check behind it (`GET /theses/switches`); bench prices in the price job and a bench section in the monthly review; the `thesis:volatility:<ticker>` finding ("thesis risk profile changed") and a per-stock risk-share finding (default 8 %); the switch log (migration `20261025100000_switch_log.sql`, `POST/GET /theses/switches/log`) feeding a real "Switches" scorecard section; chat tools `rotation_candidates` and `switch_effect`; the scenario fields in the theses editor, the ranking table and switches list on the thesis board; five glossary entries.

Verified: unit tests for scenarios, expected return, annualisation, asymmetry, the risk profile, the score, the switch action and equal-risk size, the bench lines, both risk findings, earnings moves and the switch scorecard; mypy clean; ruff clean apart from the known E501 in `sources/xbrl_concepts.py`; web vitest, eslint and tsc clean apart from the known route-type errors.

- G80. Nothing was run against live data: the migration is not applied, the routes were not called, the thesis board was not opened in a browser, and no thesis has scenarios yet, so the ranking shows its "add scenarios" note until they are entered.
- G81. The risk profile is stage A only for bench ideas (daily closes in the stock's own currency). Held stocks take their marginal risk from the weekly model (labelled stage B), but the score always divides by the stage A one-year volatility so the rows stay comparable. The stock's Expected Shortfall and factor exposures (Q8.3 stage B) are not in the profile.
- G82. "Improves the portfolio" compares a score built on local-currency volatility with the portfolio's Sharpe ratio in the base currency.
- G83. Currencies are compared as exact strings (GBX against GBP gives "not comparable"), unlike the price-target check, which normalises.
- G84. The switch scan runs one or two full pre-trade evaluations per holding, in sequence and uncached; with many holdings with scenarios it will be slow, which is why it is a button, not part of the page load.
- G85. A switch is flagged only when the pre-trade check says the book's expected return rises without its volatility rising. A trade-off switch (more return, more risk) is shown with a reason and never flagged, and a switch whose tax could not be estimated is never flagged.
- G86. When the idea is less volatile than the holding, the equal-risk purchase is capped at the sale proceeds, so risk equivalence is lost on that side.
- G87. Earnings-day moves come from the report dates of past `earnings_check` jobs, so a stock never checked has none; the move runs from the close before the report date to the close after it, not intraday.
- G88. Bench prices rely on the thesis ticker having an instrument row with a price symbol; a bench ticker without one shows "no stored price history".
- G89. The suggested starting size proportional to risk-adjusted return over volatility (Q8.6) was not built: the Q5 start size (tier cap, limits, quarter-Kelly) stands. The per-stock risk-share ceiling defaults to 8 %, so the first sync after deploy may open findings on the existing book; set `max_stock_risk_share: null` to switch it off.
- G90. Deep research does not yet pre-fill scenario drafts with citations (Q8.1); the owner enters them.
- G91. The switch log is typed in by the owner ("I made this switch"); a switch is not detected from Trading212 transactions, and the scorecard needs five switches before it reports a figure.
- G92. The monthly review's bench lines are in the stored review and its markdown; the review page does not render the risk or bench note lists as separate blocks.
- G93. The volatility-band trigger reads the sync's loaded book, so it does not run when a sync has no book (no price history), and uses three months of local-currency closes.

- G94. `test_every_free_source_migration_has_a_documented_rollback` fails on the Q4 migration `20261024100000_factor_return`: its rollback note does not say "proven by". The new `switch_log` rollback follows the same hand-written style, so it would fail the same check once the Q4 one is fixed; none of these rollbacks has been run against a database.
- G95. After the last fixes only the two affected test files were re-run, not the whole backend suite (the last full run had 2722 passed and 2 failed, one of them G94).
- G96. The pre-trade method version was raised to 1.2.0 because a thesis with scenarios now changes the expected return and the asymmetry gate. A saved pre-trade result from before keeps 1.1.0 and its old numbers; nothing recomputes it.
- G97. The pre-trade check now treats a thesis with scenarios as having no `sell_target` need, so a target holding only scenarios passes the "has a target" tests, but the sell/buy target alerts (`target:sell`, `target:buy`) still read only the price levels and never fire for it.
- G98. The ranking and switch routes load the whole stored price history of every thesis and holding (three years) on each call, with no cache; the thesis board loads the ranking on every visit.
- G99. Tests that pin a default (the per-stock risk-share ceiling, the volatility multiple) had to switch it off locally; any other test or fixture built on a small book will meet the same extra findings.
- G100. Full backend runs on this machine need `--basetemp` pointed at a folder the user owns, because the default pytest temp folder is access-denied.

### Gap closure (10 October 2026)

**Closed in code**

- G83: a price in GBX and a target in GBP (or the reverse) are one currency; the price is scaled (`fx.convert.price_in_unit`) in the ranking and in the Kelly bound. Different currencies still give "not comparable".
- G92: the review page now shows "Portfolio risk" and "Bench" blocks from `risk_notes` and `bench_notes`.
- G94: the rollbacks of the five quant migrations (`factor_return`, `risk_report_snapshot`, `risk_scenario`, `holding_risk_tier`, `switch_log`) were run against a real Postgres by `test_each_rollback_restores_the_schema_its_migration_changed` (every migration applied in order, each rollback checked against a catalog snapshot, then re-applied). Their notes now say so, and the documented-rollback test passes.
- G98: the closes read for the ranking and the switch scan are kept for two minutes (`SERIES_TTL_SECONDS`), so the board, the scan and a chat question that follow one another read the prices once.
- G44: `pretrade.equity_premium` (5 %), `pretrade.confidence_low` and `pretrade.confidence_high` (20 % and 80 %) are strategy settings. The `premium` query on the sizing route overrides the premium for one request.
- G89, in part: the Kelly bound now uses a thesis's scenarios (probability-weighted value, annualised to the catalyst) when it has them, instead of reporting "no sell target". The risk-adjusted starting size is still not built.
- G75: the strategy editor has fields for the drawdown rungs, the per-stock risk share and the volatility multiple on the thesis band.
- Bug found on the way: saving the strategy from the editor dropped `max_stock_risk_share` and `thesis_volatility_multiple` back to the backend defaults, so an owner who had switched either off (null) would have had it switched on again. Both are now form fields and round-trip, with a test.

- G95, G53, G65: the whole backend suite ran to completion (`--basetemp` set): 2731 passed, 754 skipped, 0 failed. The database-backed ones are among the skipped unless `STEWARD_TEST_DATABASE_URL` is set; the rollback proof above ran with it.

**Second round (10 October 2026)**

- Q2.1 to Q2.3 built, with the exit checks as tests (`tests/test_tail.py`: VaR and ES against the normal formulas on a large i.i.d. sample, the drawdown probability within 3 points across seeds and exactly reproducible per seed). Q2.4 and Q2.5 are partly built (above). This closes G43, G73 (the monthly review now carries the bad-month loss and the drawdown odds, and no longer says they are "not modelled") and the ES part of G74 (the thesis risk pack has the position's part of the book's loss in its worst weeks) and G81's ES gap at book level.
- G54: the deposit allocator keeps back what cash needs to reach the lower edge of the cash band (`cash_reserve`), and says so in a note.
- G56: `pretrade.event_loss_budget` (null = off) cuts the starting size so the stock's largest past move on a report date costs no more than the budget; the deposit page says when it binds.
- G57: the starting size also holds the stock's own share of volatility to `risk.max_stock_risk_share` when no pre-trade cap is set.
- Price history on the live database: `price_bar` now holds 83,793 daily rows, 48 symbols, to 9 October 2026, with 10 years for holdings. This settles G79 and the price part of G1 and G7. The 15 proxy and index symbols (`XLB`, `XLC`, `XLE`, `XLF`, `XLI`, `XLK`, `XLP`, `XLU`, `XLV`, `XLY`, `VGK`, `VT`, `VWO`, `^GSPC`, `^STOXX`) were stored as one bar a month, because Yahoo answers `range=max` with monthly bars. Fixed in code (`sources/yahoo.py` now asks for dated daily bars beyond ten years, tested). The stored monthly rows and their `price_backfill:yahoo:<symbol>` markers still have to be deleted before the next price run refetches them daily (G103).
- `switch_log` applied to the live database (G80, in part: the routes and the board have still not been used against it).

**Settled as intended, not changed**

- G97: a scenarios-only thesis has no price level, so the sell and buy target alerts have nothing to compare with. Add a sell or buy target next to the scenarios to get the alert.
- G96: a saved pre-trade result keeps the method version it was made with; recomputing history is not wanted.
- G100: `--basetemp` is a property of this machine, not of the code.

- G101 (closed). Q2 results are saved as `tail` snapshots (migration `20261026100000_tail_snapshot_kind`) at each sync and on opening the page, with their method version.
- G102 (closed). The bad-months page has a fan chart (percentile paths of the bootstrap, `fan` on each `DrawdownOdds`, `risk/tail/fan-chart.tsx`); not yet looked at in a browser.
- Third round (10 October 2026, with the owner's go-ahead): G103 closed on the live database (4,770 monthly rows and 15 `price_backfill` markers deleted, the price job re-run; daily bars of about 5,000 rows per symbol over 20 years are coming back). G91 built as a candidate list (`GET /theses/switches/detected`, sale and a buy of another stock within three days and 50 to 115 % of the proceeds; the live history holds only BUY orders, so it finds none today). G23 in part: a stock with a loss year among its latest five stored annual results (at least three needed) cannot be core; the live database holds too few annual results for this to act yet. G25: `figure_basis` on the ex-ante report gives the window, observation count and method of each headline figure. G90: the thesis draft now also writes bear/base/bull stories as events, each citing facts; prices and probabilities stay the owner's. G21: strategy saved as version 65 through the app's validation (new fields stored with defaults; `auto_tiers` is off, `max_es_95_month` and `max_drawdown_probability` are unset, so those limits do nothing until you set them). G3, G50, G80 browser checks are still not done.
- G103 (closed, see above). The monthly rows of the 15 proxy and index symbols are still in `price_bar`, with their backfill markers in `sync_state`; they were not deleted because the delete was blocked and needs the owner's say-so. Until they go, the weekly proxy backfill and the long replays for those symbols rest on monthly bars.
- G104. Q2 works on weekly returns of the book (the one-week and one-month horizon), the weights are today's, and positions left out of the model count as cash with no return, so the tail is understated by about their weight.

**Still open** (need live data, a browser, a design decision or a separate phase): G80 (live run, migration `switch_log` not applied to the live database), G81, G82, G84, G85, G86, G87, G88, G90 (deep-research pre-fill), G91, G93, G99, and the Q5, Q6, Q7 items G43, G45, G46, G48 to G79 not named above. Q2 (Expected Shortfall and drawdown probability) is not started.

## Delivery order

1. **With the early Morningstar phases:** `Q8.0`–`Q8.5`, `Q8.7` and `Q8.8` on stage A data, then `Q0 → Q1` and `Q2.1`, which move `Q8` to stage B. These carry the owner's decision loop.
2. **After the Morningstar plan:** `Q2` rest and `Q5.1` (the pre-trade before/after check, which `Q8.5` then uses in full), then `Q8.6` and `Q8.9`.
3. **Q3, Q4** together (stress shocks use factor betas); `Q4` adds the factor-tilt check to `Q8.4`.
4. **Q5 rest, Q6, Q7, Q8.10.**

Each phase ships with tests, its API in `openapi.json` (regenerated types for the frontend), and a short method note in the module docstring.

## References

- RiskMetrics Technical Document (J.P. Morgan/Reuters, 1996) — EWMA covariance.
- Ledoit & Wolf, "Honey, I Shrunk the Sample Covariance Matrix" (2004).
- Meucci, "Managing Diversification" (2009) — effective number of bets.
- Barone-Adesi, Giannopoulos & Vosper (1999) — filtered historical simulation.
- Politis & Romano, "The Stationary Bootstrap" (1994).
- Chekhlov, Uryasev & Zabarankin (2005) — Conditional Drawdown at Risk.
- Fama & French (2015) five-factor model; Ken French Data Library.
- Black & Litterman (1992); He & Litterman (1999).
- Brinson, Hood & Beebower (1986); Brinson & Fachler (1985).
- Bailey & López de Prado, "The Sharpe Ratio Efficient Frontier" (2012) — PSR, MinTRL.
- Kupiec (1995); Christoffersen (1998) — VaR backtesting.
