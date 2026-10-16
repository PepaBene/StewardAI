# Earnings multiple path

The earnings multiple path values a share by projecting revenue, margin and share count for a few years, applying an exit P/E and discounting the result to today.

## What it shows

Three scenarios (bear, base, bull) by default. Each shows a year-by-year table, an ordered calculation chain with every intermediate step, a value per share today, and the upside against the current price. It also shows the range of scenario values, a reverse solve (the exit P/E, or the constant revenue growth, that matches today's price), and a warning when saved assumptions are stale or drifting.

The inputs are the owner's assumptions, shown beside the company's own history and the peer value. The app does not forecast.

## Inputs

- Starting revenue: the owner's value, else trailing twelve months when newer than the last fiscal year, else the last fiscal year.
- Starting diluted shares: the owner's value, else the latest stored share count, else the last filed diluted (or basic) weighted shares.
- Per scenario: years (3 to 10, default 5), revenue growth per year, net margin per year, exit P/E, share count change per year.
- Discount rate: an override, or a build-up of risk-free rate, beta and equity risk premium plus an extra premium.
- Today's price, when it is in the statement currency.

A single value for growth, margin or share change applies to every year. Otherwise one value per year is required.

## Method

```
revenue(t)  = revenue(t-1) * (1 + growth(t))              t = 1..N
earnings(t) = revenue(t) * net margin(t)
shares(t)   = shares(t-1) * (1 + share change(t))
eps(N)      = earnings(N) / shares(N)
exit price  = eps(N) * exit P/E
value today = exit price / (1 + r) ^ N
```

The discount rate is the override when given, else:

```
r = risk-free + beta * equity risk premium + extra premium
```

Interim dividends are not added. The value is the discounted exit price only.

The upside is `value / price - 1`. The margin of safety of the selected scenario is `(value - price) / value`.

**Reverse solve of the exit P/E.**

```
exit P/E = price * (1 + r) ^ N / eps(N)
```

**Reverse solve of revenue growth.** A constant growth rate is found by bisection between -50 % and +100 % (80 steps) so that the value equals the price, other inputs unchanged.

**Default scenarios.** Seeds come from the company's history: revenue CAGR over up to 5 years, the median net margin of the last 5 years, the own 5-year median P/E (else current P/E, with peer values shown beside), and last year's share change. The defaults are clamped and spread:

```
base growth = seed growth, clamped to -5 % .. 15 % (5 % if none)
bear growth = base - 4 points;  bull growth = base + 4 points
base margin = seed margin, else peer margin, else 10 %; clamped to 1 % .. 60 %
bear margin = 0.8 * base;  bull margin = 1.1 * base
base exit P/E = peer median, else own, else 15; clamped to 5 .. 40
bear exit P/E = 0.8 * base;  bull exit P/E = 1.2 * base
share change = seed, clamped to -5 % .. +5 %
bear extra premium = 1 point
```

**Staleness and drift.** Saved assumptions are stale after a set number of days. They drift when the last four quarters' revenue growth, or net margin, departs from the selected scenario's first-year assumption by more than the band. Revenue growth compares the last four quarters with the four before.

## Thresholds and defaults

| Setting | Default |
| --- | --- |
| Horizon | 5 years (3 to 10) |
| Risk-free rate | 4 % |
| Equity risk premium | 5 % |
| Beta | 1 |
| Extra premium | 0 |
| Discount rate bounds when overridden | 1 % to 40 % |
| Exit P/E bound | above 0, up to 200 |
| Net margin bound | between -100 % and 100 % |
| Revenue growth bound | above -100 % |
| Scenarios saved | up to 5 |
| Stale after | 90 days (`stale_after_days`) |
| Drift band | 5 points (`drift_band`) |

## When it cannot be computed

- No starting revenue is filed: the scenario value is unavailable ("starting revenue is not filed").
- No share count is stored: unavailable ("no share count is stored").
- Forecast earnings in the exit year are not positive: unavailable ("n/m").
- The price is in another currency than the statements: the price comparison and the reverse solve are withheld, with a warning.
- The exit P/E reverse solve is unavailable when eps in the exit year is not positive.
- The growth reverse solve is unavailable when even -50 % growth gives more than the price, or when even +100 % growth gives less than the price.

## Limits

- The result is only as good as the assumptions. It is not a forecast.
- Net margin is applied to revenue directly. No tax, capital or working-capital detail is modelled.
- One exit P/E is applied at the end. Interim dividends are ignored.
- Drift compares only the first-year assumption with the last four quarters.

## Golden examples

Test: `apps/backend/tests/test_multiple_path.py::test_golden_hand_calculation`. Revenue 100, growth 10 %, margin 10 %, 3 years, 10 shares, exit P/E 20, discount rate 10 %.

- Revenue after 3 years is 100 x 1.1^3 = 133.1.
- Earnings are 133.1 x 10 % = 13.31.
- EPS is 13.31 / 10 = 1.331.
- Exit price is 1.331 x 20 = 26.62.
- Value today is 26.62 / 1.1^3 = 20.00.

Other tests in the same file: a 2 % yearly buyback gives 20 / 0.98^3; per-year paths 20 %, 10 %, 0 % growth give revenue 120, 132, 132 and earnings 26.4; the reverse solves return the price (exit P/E 30 for a price of 30, growth 10 % for a price of 20).

## Version

Method `multiple_path` version 1.0.0 (`apps/backend/src/steward/analytics/versions.py`).
