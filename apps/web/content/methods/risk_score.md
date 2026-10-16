# Risk score

The risk score is one number from 0 to 100 that places the portfolio's risk against the market, built from five risk figures.

## What it shows

A score with a band, the five sub-scores behind it, and the benchmark scored on the same references. A score of 50 means as risky as the benchmark on every input. The risk page also shows metric tiles with a one-line verdict, volatility, drawdown and beta per holding, and the pairs of holdings that moved most and least together.

## Inputs

All five are sizes of risk, where larger means riskier. Four come from the risk report and one from the X-Ray.

| Input | Figure | Market reference (sub-score 50) | Very high (sub-score 100) |
| --- | --- | --- | --- |
| `volatility` | annualised standard deviation of daily returns | benchmark's, same window | 40 % |
| `max_drawdown` | worst peak-to-trough fall, as a positive size | benchmark's, same window | 50 % |
| `expected_shortfall` | 95 % one-day historical expected shortfall | benchmark's, same window | 6 % |
| `concentration` | share of the three largest issuers | 12 % | 60 % |
| `beta` | beta against the benchmark | 1.0 | 2.0 |

The benchmark's figures are the same statistics applied to the benchmark series over the days the portfolio has. Concentration uses look-through issuers from the X-Ray. When the X-Ray cannot be loaded, it falls back to the three largest direct position weights, where a fund counts as one holding and concentration is understated.

## Method

Each input is placed on a two-piece line, clamped to 0..100:

```
value <= reference:  sub-score = value / reference * 50
value >  reference:  sub-score = 50 + (value - reference) / (very high - reference) * 50
```

A negative value is treated as zero risk. The very-high level is the larger of the fixed level above and 1.5 times the reference, so a very risky benchmark still leaves room.

The score is the weighted sum:

```
score = 0.30 * volatility + 0.25 * max drawdown + 0.20 * expected shortfall
        + 0.15 * concentration + 0.10 * beta       (each as a sub-score)
```

The score is rounded to one decimal. The bands are:

| Score | Band |
| --- | --- |
| below 35 | Lower risk than the market |
| 35 to below 55 | About as risky as the market |
| 55 to below 75 | Riskier than the market |
| 75 and above | Much riskier than the market |

The benchmark score is computed the same way with the benchmark's own figures. Concentration and beta use their fixed references for the benchmark.

## Thresholds and defaults

| Setting | Value |
| --- | --- |
| Weights | volatility 0.30, drawdown 0.25, expected shortfall 0.20, concentration 0.15, beta 0.10 |
| Fixed very-high levels | 0.40, 0.50, 0.06, 0.60, 2.0 |
| Fallback references (benchmark has no figure) | volatility 0.16, drawdown 0.20, expected shortfall 0.025 |
| Fixed references | concentration 0.12, beta 1.0 |
| Band limits | 35, 55, 75 |
| Minimum observations | set by the risk report; a figure below its minimum is withheld |

## When it cannot be computed

The score is `unknown`, never partial, when any one input is missing. The reason names the missing inputs, for example "Not scored: Expected shortfall (95 %, one day) is not available yet." Each sub-score carries the reason the risk report gave, such as too few observations.

When the benchmark has no figure (or a figure at or below zero) for volatility, drawdown or expected shortfall, the fallback reference is used and the sub-score says so.

## Limits

- The score is relative to the chosen benchmark and window. A calm window understates risk.
- Historical figures describe the past. They do not predict losses.
- The weights and the very-high levels are fixed choices of this method, not estimates.
- Fund holdings count as one issuer only in the fallback concentration.

## Golden examples

Test: `apps/backend/tests/test_risk_score.py::test_golden_portfolio`. Benchmark volatility 0.16, drawdown 0.20, expected shortfall 0.025. Portfolio volatility 0.24, drawdown 0.10, expected shortfall 0.025, concentration 0.30, beta 1.2.

- Volatility: 50 + (0.24 - 0.16) / (0.40 - 0.16) x 50 = 66.667.
- Drawdown: 0.10 / 0.20 x 50 = 25.
- Expected shortfall equals the benchmark: 50.
- Concentration: 50 + (0.30 - 0.12) / (0.60 - 0.12) x 50 = 68.75.
- Beta: 50 + (1.2 - 1) / (2 - 1) x 50 = 60.
- Score = 0.30 x 66.667 + 0.25 x 25 + 0.20 x 50 + 0.15 x 68.75 + 0.10 x 60 = 52.5625, shown as 52.6.
- Band "About as risky as the market". Benchmark score 50.0. Sub-scores shown as 66.7, 25.0, 50.0, 68.8 and 60.0.

Other tests in the same file check that the score never falls as any input rises, that a sub-score is 0 at zero, 50 at the reference and 100 at the very-high level, and that a missing input gives `unknown`.

## Version

Method `risk_score` version 1.0.0. The verdict phrases on the tiles use method `verdict_phrases` version 1.0.0 (`apps/backend/src/steward/analytics/versions.py`).
