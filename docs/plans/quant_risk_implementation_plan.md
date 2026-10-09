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
- G5. The Ledoit-Wolf "golden test against the reference implementation" (engineering rule 4) is a test against a line-by-line transcription of the authors' published `covCor` formulas, not against their code or a library output. The EWMA test is likewise against a loop written for the test. Add a fixture with output from a published implementation.

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

- [ ] `Q2.1` `quant/tail.py`: historical, filtered-historical and Cornish-Fisher VaR and ES at 95/99 %, 1-day and 1-month horizons, per portfolio with each position's ES contribution.
- [ ] `Q2.2` `quant/bootstrap.py`: stationary block bootstrap of the current book over 1, 2 and 3 years (10 000 paths, fixed seed per run so a result is reproducible); outputs P(max drawdown ≥ 20 %), max-drawdown quantiles, terminal value quantiles, Conditional Drawdown at Risk, median time to recover a 20 % fall.
- [ ] `Q2.3` Downside statistics on realized history: Sortino, Calmar, Ulcer index, downside deviation, skew and kurtosis — completes `M6.1`.
- [ ] `Q2.4` Nightly worker job storing results with `method_version`; new limits from D5 evaluated after it.
- [ ] `Q2.5` Frontend: drawdown fan chart over the horizon with the 20 % line, ES card, "what a 1-in-20 month looks like" in CZK.

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
- [ ] `Q4.4` Use factor exposures in "propose new ideas" (principle 7c): rank candidates partly by how much they reduce the dominant factor tilt.
- [x] `Q4.5` Frontend: factor exposure chart for portfolio vs benchmark, per-holding table.

**Exit check:** the benchmark ETF regressed on its own region's market factor gives beta ≈ 1 and R² > 0.9.

### Status of Q3 and Q4 (implemented 9 October 2026) and the gaps that remain

Code: `quant/{stress,factors,french,french_store}.py`, `portfolio/{replay,factors}.py`, migration `20261024100000_factor_return.sql`, job `factor_returns` (Mondays 05:30), `GET /portfolio/risk/stress-replay` and `GET /portfolio/risk/factors?region=`, web tabs "Past episodes" (`/risk/replay`) and "Factors" (`/risk/factors`). Tests: `tests/test_stress.py`, `tests/test_factors.py`, `lib/risk/{replay,factors}.test.ts`. Built: `Q3.1`, `Q3.4`, `Q4.1`, `Q4.2`, `Q4.3`, `Q4.5`. Not built: `Q3.2`, `Q3.3`, `Q4.4` (below). The Q4 exit check is met on synthetic data (benchmark = market plus noise gives beta 1.0 ± 0.05 and R² > 0.9); the Q3 exit check holds by construction (`after = before + Σ position results`, tested) and every proxied position is labelled.

**Not built**

- G30. `Q3.2` hypothetical shocks through the factor betas: not started. `decision/shocks.py` (M6.2) already applies owner-stated shocks with measured market betas; routing equity, currency and rates shocks through the Q4 loadings is the change, and needs the rates factor (G34).
- G31. `Q3.3` saved scenarios and snapshots: not started. The replay report is computed live and not stored (engineering rule 5, same as G26).
- G32. `Q4.4` factor-aware idea ranking (principle 7c): not started; the tilt is available per factor in the API response.

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

## Phase Q5 — Sizing, deposits and the pre-trade check

**Outcome:** the decisions the owner already makes, with risk numbers attached before acting in Trading212.

- [ ] `Q5.1` Pre-trade "what-if" endpoint: given proposed buys/sells or a deposit, return the full rule and risk-limit evaluation after the trade (caps, tiers, ex-ante vol, ES, drawdown probability, correlation, risk budgets, effective bets) as a before/after diff.
- [ ] `Q5.2` Risk-aware starting size for a new position (method table), shown next to the principle 8b cap.
- [ ] `Q5.3` Expected-return input from thesis and valuation, shrunk toward equilibrium; ¼-Kelly upper bound displayed with its inputs.
- [ ] `Q5.4` Deposit allocator (principle 8c): ranked allocation of a given CZK amount, whole shares, all constraints, risk impact of each line; read-only.
- [ ] `Q5.5` Rule 9 helper: for a position above 10 %, show the dilution path from expected deposits and the trim size to 8 % when the thesis is weakened.
- [ ] `Q5.6` Chat tools: "what does buying X do to my risk?", "where should my next deposit go?" — the assistant calls Q5.1/Q5.4 and cites the computed result.

**Exit check:** an allocation never breaches a cap in the post-trade evaluation; every proposal cites the rule or KPI it serves (as the monthly review already requires).

## Phase Q6 — Process evaluation and model validation

**Outcome:** evidence on whether the risk model is right and whether the thesis process adds value.

- [ ] `Q6.1` VaR backtest (Kupiec, Christoffersen) and volatility-forecast calibration on rolling history; results on the Activity page and in the monthly review. A failed test lowers the model's displayed confidence and opens a review item.
- [ ] `Q6.2` Brinson-Fachler attribution by sector and tier vs the benchmark, monthly and since inception, reconciled to total excess return.
- [ ] `Q6.3` Factor attribution: alpha net of the Q4 factors, with its t-statistic.
- [ ] `Q6.4` Probabilistic Sharpe Ratio vs the benchmark's Sharpe and Minimum Track Record Length; shown as "not yet distinguishable from the benchmark" where applicable.
- [ ] `Q6.5` Decision journal metrics (with `M7.1`): per closed thesis, return vs benchmark over the holding period, hit rate by tier, and whether risk-review triggers preceded losses.

**Exit check:** Brinson effects sum to the measured excess return within 1 bp.

## Phase Q7 — Integration into reviews and alerts

- [ ] `Q7.1` Monthly review: a risk section with forecast vs realized volatility, top risk contributors, tier risk budgets, ES, drawdown probability, worst stress scenario, factor tilts and model-validation status; proposed actions must cite a rule, KPI or risk limit.
- [ ] `Q7.2` Thesis review pack: the position's risk contribution, ES contribution, correlation cluster and stress losses.
- [ ] `Q7.3` Drawdown early-warning ladder ahead of principle 4g: notes at 10 % and 15 % drawdown that list the positions driving it and the theses due for re-confirmation; the 20 % freeze stays as written.
- [ ] `Q7.4` Glossary entries for every new measure, written in the owner's terms.

## Phase Q8 — The decision loop: expected return, risk-adjusted ranking and rotation

**Dependencies:** none to start (stage A uses today's per-stock risk data: daily closes, `reference/risk.py` volatility, maximum drawdown and correlation, the CZEONIA rate and the portfolio's Sharpe ratio from `portfolio/risk.py`); `Q1` and `Q2.1` upgrade it (stage B). The thesis price target from the current code (`ThesisPriceTarget`: sell and buy targets, currency, method, date) is the starting point. **Outcome:** strategy principles 5–9 and 11b computed in the app, so the owner sees every holding and bench idea ranked by risk-adjusted expected return, with switch flags, before acting in Trading212.

- [ ] `Q8.0` Exempt list on the single-position rule (principle 2a): an optional `exclude: list[Ticker]` on `PositionWeightRule` (rejected when the rule names a single ticker), skipped by the `position_weight` evaluation in `portfolio/breaches.py`, with an "Except (optional)" field in the strategy editor and its form round-trip. The owner then lists the core ETFs in `max-single-position`, so they stop raising the 10 % alert while their holdings still count toward the look-through limit (principle 4c). Tests: an excluded ticker above the cap raises nothing, others still breach, a single-ticker rule with `exclude` fails validation.
- [ ] `Q8.1` Thesis scenario fields (principle 5), added to `ThesisPriceTarget` and the theses editor: bear, base and bull values per share with probabilities (defaults 25 / 50 / 25 %; values must rise from bear to bull and probabilities add up to 100 %); a catalyst date; and the expected volatility band. Allow a target with scenarios only. Extend `ThesisPriceTarget.describe()` so deep research and the severe-news impact notes see the scenarios, and let deep research pre-fill drafts with citations for the owner to confirm.
- [ ] `Q8.2` Expected-return module (pure functions, golden-tested): probability-weighted expected value and expected return to the current price; annualized to the catalyst date (2 years when unset, floor 3 months); base-case upside to bear-case loss ratio; flags for the return hurdle (default 30 % over the horizon) and the asymmetry gate (default 2:1) of principle 8a. Thresholds become strategy config fields (`risk` block or a new `decision` block, null = off), not constants.
- [ ] `Q8.3` Risk profile per holding and bench idea (principles 5d and 9c). Stage A: 1-year and 3-month volatility, maximum drawdown, correlation with the portfolio's time-weighted index, and marginal risk approximated as correlation × volatility; the bear-case loss compared with the historical maximum drawdown. Stage B: weekly CZK returns (`Q0`), marginal contribution to portfolio risk from the covariance model (`Q1`), the stock's Expected Shortfall (`Q2.1`) and its factor exposures (`Q4`). Every figure carries its window, observation count and stage.
- [ ] `Q8.4` Risk-adjusted ranking (principle 7c): `GET /theses/expected-returns` returning, for every thesis with scenarios, the price and its date, expected return (total and annualized), risk profile, risk-adjusted score ((annualized expected return − cash rate) ÷ standalone volatility in stage A, ÷ marginal risk in stage B), the "improves the portfolio" flag (risk-adjusted return above the portfolio's × correlation), hurdle and asymmetry flags, and the reason for any missing figure. A ranking table on the thesis board, holdings and bench ideas together, with the formula and inputs on drill-down. After `Q4`, an idea that adds to the dominant factor bet shows the higher expected return it needs.
- [ ] `Q8.5` Switching rule (principle 9f): for each holding against the best-ranked idea, the expected-return gap over the horizon against the switching hurdle: tax on the exact lots sold (from `M4.8`; 15 % of the lot's gain when under 3 years and above the annual exemption), trading and FX costs, and the safety margin (config, default 5 percentage points). Show the suggested action (hold, partial or full switch, scaled by how far the gap clears the hurdle), the "switch now or after the time test" comparison when a lot passes 3 years within 6 months, and the risk-equivalent size of the replacement (old weight × old volatility ÷ new volatility, remainder to the next idea or the core ETFs). Run the switch through the `Q5.1` before/after evaluation; flag it only when the portfolio's risk-adjusted expected return improves with every limit held. Flags only: nothing is traded.
- [ ] `Q8.6` Sizing and risk budgets (principles 4e and 8b): a per-stock cap on contribution to portfolio volatility (config, default 8 %), checked after each sync as a review-trigger finding once `Q1` exists; the suggested starting size (∝ risk-adjusted expected return ÷ volatility, ceiling only) next to the tier cap in the pre-trade check; the stock's historical moves on earnings days, so a position ahead of a binary event can be sized down.
- [ ] `Q8.7` The bench (principles 7d and 8c): theses for tickers not held are the bench; give them prices (the price job fetches daily closes for bench tickers as it does for holdings), show the bench count against the 5–10 target in the monthly review, and route sale proceeds and deposits to the best bench idea above the hurdle, else the core ETFs, in the deposit allocator (`Q5.4`).
- [ ] `Q8.8` Volatility-band review trigger (principle 9c): after each sync, a held stock whose 3-month realized volatility is above 1.5 × its thesis band opens a `thesis:volatility:<ticker>` finding (kind `rule`, severity 2, its own alert headline "thesis risk profile changed") through the existing breach machinery; it clears when volatility falls back inside.
- [ ] `Q8.9` Switch log and scorecard (principles 9f and 11b): record each switch the owner confirms (stock sold, stock bought, date, both expected returns and risk profiles, hurdle, lots and tax); the yearly scorecard compares the bought stock with the sold one from the switch date and recommends raising the safety margin when switches do not beat holding (built with `M7.9`).
- [ ] `Q8.10` Chat tools: "what should I rotate?", "where should this sale's proceeds go?", "what does switching X for Y do to my risk?"; the assistant calls `Q8.4`, `Q8.5` and `Q5.1` and cites the computed results.

**Exit checks:** golden examples reproduce the expected return, annualization and asymmetry ratio by hand (including the owner's Google example: held at 220 USD, scenarios 170 / 230 / 270 at 25 / 50 / 25 % → about +2 %; a candidate at 50 with 40 / 70 / 90 → +35 %; hurdle about 10 points → full switch at about 5 % risk-equivalent size for a 28 % → 45 % volatility change); a ranking row with a missing input says why instead of showing a number; a switch flag never appears when the post-trade evaluation breaks a limit; stage A and stage B figures are labelled as such.

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
