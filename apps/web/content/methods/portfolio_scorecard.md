# Portfolio scorecard

The portfolio scorecard gives each of the five check axes a score for the whole portfolio: the value-weighted mean of the holdings' axis scores.

## What it shows

For each axis (value, future, past, health, dividend) it shows a score from 0 to 6, how many holdings and how much of the portfolio value the score covers, and the five holdings lifting and the five holding back the score. It also lists each holding's axis scores and the holdings that have never been checked.

## Inputs

- The latest position snapshot: ticker and value in the account currency, for holdings with a positive quantity and a positive value.
- The latest cached axis score of each holding, from the company checks (see the checks method).

The scorecard does not recompute any check. A holding with no cached checks is listed as unchecked.

## Method

A holding's weight is its share of the total snapshot value:

```
weight = holding value / total value of all listed holdings
```

For one axis, only holdings with a score count (holdings whose axis has no applicable check are left out):

```
score = sum(weight * holding score) / sum(weight)       over scored holdings only
value covered = sum(weight of scored holdings) / sum(weight of all holdings)
```

The score is rounded to 2 decimals and the value covered to 4 decimals. The coverage text reads "N of M holdings, P% of value".

The holdings lifting the score are the five scored holdings with the highest score (ties broken by larger weight). The holdings holding it back are the five with the lowest score.

Overall status:

- `unavailable` when there are no holdings or no axis has a score.
- `complete` when every holding has cached checks and every axis covers every holding.
- `partial` otherwise.

For a saved screen's mark, the same cached scores are averaged with equal weights instead (the members' mean to 2 decimals).

## Thresholds and defaults

| Setting | Value |
| --- | --- |
| Score scale | 0 to 6 per axis |
| Weights | share of the latest snapshot value |
| Holdings listed as lifting or holding back | 5 each |
| Minimum coverage to show a score | none; coverage is stated |

## When it cannot be computed

- An axis score is empty when no holding has a score on that axis.
- The whole scorecard is `unavailable` when there are no holdings or no axis has a score.
- A holding with no applicable check on an axis is not counted in that axis and lowers the value covered.

## Limits

- The score reflects only covered holdings. A high score with low coverage says little about the rest.
- Cached scores may be older than the latest filings. Each holding shows when it was last checked.
- Weights use the latest snapshot value, not cost.
- A mean score hides the spread. Read the lifting and holding-back lists with it.

## Golden examples

Test: `apps/backend/tests/test_insight_golden.py::test_scorecard_axis_is_the_value_weighted_mean_over_scored_holdings`.

- Holding A has weight 0.5 and scores 6 of 6, giving 6.00.
- Holding B has weight 0.3 and passes 3 of 6, giving 3.00.
- Holding C has weight 0.2 and no applicable check, so it is not scored.
- Score = (0.5 x 6 + 0.3 x 3) / (0.5 + 0.3) = 3.9 / 0.8 = 4.875, shown as 4.88.
- Holdings covered 2 of 3, value covered 0.8000, coverage text "2 of 3 holdings, 80% of value".
- Lifting list is A then B.

## Version

Method `portfolio_scorecard` version 1.0.0 (`apps/backend/src/steward/analytics/versions.py`).
