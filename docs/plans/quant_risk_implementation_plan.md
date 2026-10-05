# StewardAI: quantitative finance and risk management implementation plan

Research snapshot: 5 October 2026. **Sequencing:** implemented after the [Morningstar-style analytics plan](morningstar_features_implementation_plan.md) is complete; re-check the "What exists today" table against the code at that point, since that plan touches the same risk, performance and scenario areas. Inputs: the owner's strategy (`strategy/strategy.json`, principles 1–16 and the `risk` block), the existing risk code (`steward/portfolio/risk.py`, `steward/portfolio/risk_limits.py`, `steward/reference/risk.py`) and the [Morningstar-style analytics plan](morningstar_features_implementation_plan.md), whose Phase 6 items `M6.1`, `M6.2` and `M6.5` this plan implements in more depth. Where the two overlap, this plan's tasks replace them.

## Goal and the line this plan does not cross

The owner's strategy is fundamental and thesis-driven. Price drives a trade in two cases only (principle 4): a position reaching its thesis price target is sold unless a re-researched thesis raises the target (principle 17), and a holding with an intact thesis may be added to after a 15 % fall (principle 18). "A price fall alone is never a reason to sell", and "a risk-limit breach only forces a review of the thesis; it is never a sell signal on its own" (principle 6). The horizon is 2–3 years and capital preservation is explicit: a deep drawdown may not recover in time.

So quant finance here does **not** mean signals, timing or automated trading. It does three jobs:

1. **Measure risk before it happens** (ex-ante), not only after (realized). Answer: "what is this portfolio's risk *today*, where does it come from, and what does a proposed trade do to it?"
2. **Size and allocate** within the owner's caps: starting sizes, deposit allocation (principle 10), tier balance — so that risk, not only weight, is diversified.
3. **Test the process honestly**: whether the risk model is calibrated, and whether thesis decisions add value against the benchmark, with the statistical noise of a small, short-horizon portfolio made visible.

Every quant output is an input to a thesis review, the monthly review, the pre-trade check or the deposit plan. None of them is a sell or buy instruction.

## What exists today and where it falls short

| Area | Today | Gap a quant approach closes |
| --- | --- | --- |
| Portfolio volatility (limit: 20 %) | Realized stdev of the flow-adjusted TWR index over the last year (`portfolio/risk.py`) | Backward-looking: mixes positions sold months ago with today's book, and cannot evaluate a proposed trade. Needs a **forecast** from current weights × covariance. |
| Pair correlation (limit: 0.85) | Pearson of daily local-currency returns, last year (`risk_limits.pair_correlations`) | Daily closes of a US and a European listing are non-synchronous, which biases cross-market correlation down. Returns are in local currency, not CZK, so FX co-movement is invisible. Pairwise only; a cluster of five 0.7-correlated names never trips it. |
| Drawdown (limit: 20 % from 1-year peak) | Current drawdown of the TWR index | Reports where we are, not how likely a 20 % drawdown is over the horizon. |
| Sharpe, beta | Sharpe vs CZEONIA, beta vs the first benchmark | No significance test (a Sharpe estimated over 1–2 years is mostly noise), no downside measures. |
| Risk tiers (principle 2) | Set by hand; criteria include "annualised volatility above 35 % / at most 25 %" | The volatility criterion is computable and not computed or alerted when it crosses. |
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
| Factor exposures | Time-series regression of each holding and the portfolio on Fama-French 5 factors + momentum (Ken French Data Library, Developed / Europe / US, free daily and weekly CSVs), plus currency (USD/CZK, EUR/CZK) and a rates factor (change in 10-year yield) | Reveals hidden concentration (e.g. every "growth" holding is the same long-momentum, short-value bet) that sector and country caps miss. Feeds principle 7. |
| Position sizing | Risk-aware starting size: min(tier start cap from principle 15b, size where the position's marginal contribution to ex-ante volatility stays within its tier's risk budget). Fractional Kelly (¼) from the thesis's expected return only as an **upper bound** shown alongside, never as a target | Keeps the owner's caps authoritative; sizing by risk makes a 1.5 % speculative position and a 5 % core position comparably risky. |
| Deposit allocation (principle 10) | Small constrained optimizer: allocate a deposit to minimise the distance to tier/sector/country under-representation, subject to all caps, no buy above cap, a ceiling on ex-ante volatility, and whole-share rounding; ranked candidate list as output | Implements principle 10 mechanically and shows the risk impact of each choice. |
| Expected returns (only where needed) | From the owner's thesis and the valuation module (implied return to fair value over the horizon), shrunk toward the benchmark's equilibrium return (Black-Litterman style, with confidence from thesis status) | The only expected returns that fit a fundamental strategy. Used for Kelly bounds and deposit ranking, never for timing. |
| Performance evaluation | Brinson-Fachler attribution vs the benchmark by sector and tier; factor attribution (alpha vs factor returns); Probabilistic Sharpe Ratio and Minimum Track Record Length (Bailey & López de Prado); Sortino, Calmar, Ulcer index, tracking error, information ratio | Separates skill from factor exposure and from luck, and says how long a track record needs to be before a Sharpe ratio means anything. |
| Model validation | VaR backtest: Kupiec proportion-of-failures and Christoffersen independence tests; volatility forecast vs realized (bias and Mincer-Zarnowitz regression) | A risk number with no calibration record is not trusted. |

### Not doing, and why

- **Mean-variance optimisation of the whole portfolio.** Expected-return error dominates; the owner's book is chosen by thesis, not by an optimizer. The deposit optimizer is deliberately small and constrained.
- **Trend, momentum or volatility-timing signals.** Principle 4 allows price-driven trades only on a reached thesis target or an add on weakness, not on trend. Volatility regime appears only as context in reviews.
- **Hierarchical risk parity / risk parity as a target.** Useful for systematic books; here, risk contributions are diagnostic, and tier risk budgets are the only "parity" idea used.
- **Option-based hedging, leverage, shorting.** Excluded by principle 15a.
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
- D4/D5 limits become new optional `RiskLimits` fields (null = off). When each is implemented (effective bets and tier budgets in Q1.3, ES and drawdown probability in Q2.4), add it to `strategy/strategy.json` and `strategy/strategy-import.json` with the value below, and extend principle 6 in the strategy notes to name it. Nothing has been changed in the strategy files yet.

| # | Decision | Accepted |
| --- | --- | --- |
| D1 | Add `numpy` and `scipy` to the backend | **Yes**: numpy in Q0, scipy in Q5. Pure-Python covariance and Monte Carlo would be slow and harder to verify. |
| D2 | Price `history_years` for holdings, benchmarks and proxy ETFs | **10 years** (from 2). Covers 2020, 2022, the April 2017 end of the ČNB's CZK floor and the late-2018 sell-off; 2008/2011 replays still come from proxy ETFs/indices only. |
| D3 | Which volatility the 20 % `max_volatility` limit tests | **Both**: ex-ante forecast (higher of EWMA and Ledoit-Wolf) as the primary, realized as before. A breach of either forces a thesis review, never a sale. |
| D4 | Tier risk budgets (share of ex-ante volatility per tier) | **Core ≤ 45 %, growth ≤ 45 %, speculative ≤ 25 %** of risk; review the values after the first month of data. |
| D5 | New review-trigger limits, severity 2 | **1-month 95 % ES ≤ 12 %; P(drawdown ≥ 20 % within 2 years) ≤ 25 %; effective number of bets ≥ 4.** Recalibrate after Q2 has produced a month of results. |
| D6 | Automate the volatility criterion of the risk tiers (principle 2) | **Compute and flag only**: a holding above 35 % volatility not tiered speculative, or a core holding above 25 %, raises a review; the owner reclassifies by hand. |

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

- [ ] `Q3.1` `quant/stress.py`: historical replay of the episodes in the method table on current weights, using real series where they exist and proxies otherwise; result per position and in total, with proxied share shown.
- [ ] `Q3.2` Hypothetical shocks through the factor betas of Q4 (equity, USD/CZK, EUR/CZK, rates, sector); owner-set sensitivities allowed where history is inadequate, labelled as owner assumptions.
- [ ] `Q3.3` Saved scenarios with their assumptions; each scenario run is snapshotted.
- [ ] `Q3.4` Frontend stress table: scenario, portfolio loss, worst three contributors, whether the 20 % drawdown limit would be hit.

**Exit check:** shocked position values sum to the shocked portfolio total; every proxied loss is labelled.

## Phase Q4 — Factor exposures

**Outcome:** hidden common bets made visible.

- [ ] `Q4.1` Ingest the Ken French Data Library factor files (Developed, Europe, US: 5 factors + momentum, daily and weekly) as a macro source with the existing source-status and coverage tracking.
- [ ] `Q4.2` `quant/factors.py`: rolling (2-year weekly) regressions per holding and for the portfolio, with t-statistics, R² and Newey-West standard errors; currency and rates factors added.
- [ ] `Q4.3` Portfolio factor tilt vs the benchmark's tilt; flag a tilt beyond ±0.3 as a concentration note in the monthly review (not a breach).
- [ ] `Q4.4` Use factor exposures in "propose new ideas" (principle 7): rank candidates partly by how much they reduce the dominant factor tilt.
- [ ] `Q4.5` Frontend: factor exposure chart for portfolio vs benchmark, per-holding table.

**Exit check:** the benchmark ETF regressed on its own region's market factor gives beta ≈ 1 and R² > 0.9.

## Phase Q5 — Sizing, deposits and the pre-trade check

**Outcome:** the decisions the owner already makes, with risk numbers attached before acting in Trading212.

- [ ] `Q5.1` Pre-trade "what-if" endpoint: given proposed buys/sells or a deposit, return the full rule and risk-limit evaluation after the trade (caps, tiers, ex-ante vol, ES, drawdown probability, correlation, risk budgets, effective bets) as a before/after diff.
- [ ] `Q5.2` Risk-aware starting size for a new position (method table), shown next to the principle 15b cap.
- [ ] `Q5.3` Expected-return input from thesis and valuation, shrunk toward equilibrium; ¼-Kelly upper bound displayed with its inputs.
- [ ] `Q5.4` Deposit allocator (principle 10): ranked allocation of a given CZK amount, whole shares, all constraints, risk impact of each line; read-only.
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
- [ ] `Q7.3` Drawdown early-warning ladder ahead of principle 14: notes at 10 % and 15 % drawdown that list the positions driving it and the theses due for re-confirmation; the 20 % freeze stays as written.
- [ ] `Q7.4` Glossary entries for every new measure, written in the owner's terms.

## Delivery order

1. **Q0 → Q1** first: the return matrix and ex-ante decomposition change the most decisions and fix the two measurement issues (backward-looking volatility, asynchronous correlation).
2. **Q2 and Q5.1** next: drawdown probability and the pre-trade check speak directly to capital preservation.
3. **Q3, Q4** together (stress shocks use factor betas).
4. **Q5 rest, Q6, Q7.**

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
