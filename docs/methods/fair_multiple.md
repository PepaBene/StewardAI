# Fair multiple

The fair multiple is an indicative P/E for a company, read from a least-squares line fitted across its peers' P/E, revenue growth and net margin.

## What it shows

A fair P/E, the peer points behind it with their fitted values, the regression coefficients and R squared, and an implied price. It is labelled "Indicative". It is shown only when the fit explains enough of the spread between peers.

## Inputs

- For each peer in the comparison (the company itself is excluded): P/E, 3-year revenue CAGR and net margin, from the latest fiscal year.
- The company's own 3-year revenue CAGR, net margin and P/E.
- The latest closing price and its currency.
- The minimum R squared from the config (`fair_multiple_min_r2`).

A peer is used only when all three figures exist and its P/E is above 0 and below 200.

## Method

The fit is ordinary least squares with an intercept:

```
P/E = a + b * revenue growth (3-year CAGR) + c * net margin
```

R squared is:

```
R squared = 1 - sum of squared residuals / total sum of squares of peer P/E
```

The fair P/E and the implied price are:

```
fair P/E = a + b * company growth + c * company margin
implied price = price * fair P/E / company P/E       (company P/E above 0, price known)
```

The implied price is the price scaled by the ratio of fair to current P/E.

## Thresholds and defaults

| Setting | Default |
| --- | --- |
| Minimum peers with a P/E, growth and margin | 5 (the standard minimum for a peer median) |
| Peer P/E range used | above 0, below 200 |
| Minimum R squared (`fair_multiple_min_r2`) | 0.3 |
| Label | Indicative |

## When it cannot be computed

The reason is stated in each case.

- Peer ratios are not computed.
- Fewer than 5 peers have a positive P/E, growth and margin.
- Peer growth and margin do not vary enough to fit (fewer than 4 points, a design that is not full rank, or no spread in peer P/E).
- R squared is below the minimum. The coefficients and points are still shown, with the reason.
- The company has no filed 3-year growth or net margin.
- The fit gives a non-positive fair P/E for the company.

## Limits

- It is a cross-section of peers, not a forecast. Growth and margin explain only part of why P/E differs.
- Few peers make the line unstable. A high R squared on five points can still be chance.
- The implied price assumes earnings per share stay as they are and only the multiple changes.
- The peer group and scope are stated in the footer of the picture.

## Golden examples

Test: `apps/backend/tests/test_valuation_pictures.py::test_fair_multiple_fit_recovers_a_linear_rule`.

- Six peers are built from the exact rule P/E = 5 + 50 x growth + 40 x margin, with (growth, margin) of (0.05, 0.10), (0.10, 0.15), (0.20, 0.05), (0.15, 0.20), (0.00, 0.30) and (0.25, 0.25).
- The fit returns a = 5, b = 50, c = 40 (each within 0.000001) and R squared above 0.999.
- With only three points the fit returns nothing.

No test pins the implied price or the minimum-R-squared gate. Those behaviours are described from the code.

## Version

Method `fair_multiple` version 1.0.0 (`apps/backend/src/steward/analytics/versions.py`).
