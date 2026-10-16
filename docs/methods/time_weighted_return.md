# Time-weighted return

How the investments did over a period, with the owner's deposits and withdrawals taken out, so that adding money never reads as a gain and taking it out never reads as a loss.

## What it shows

The return for each window (1 month, year to date, 1 year, 3 years, 5 years and since the first stored day) on the Overview and Performance pages, and the same figure for the benchmark over the same days.

## Inputs

- The account's value on each stored day, in the base currency, cash included.
- The deposits and withdrawals of each day (flows). Dividends, fees and trades stay inside the account and are not flows.
- The closes of the benchmark index, for the comparison.

## Method

Each pair of consecutive stored days is one subperiod. A flow is treated as arriving at the start of its day, before that day's market move, so the new money is part of the base its gain is measured on:

growth = 1 + (closing value - flow - opening value) / (opening value + flow)

The window's return is the product of the subperiods' growth, minus 1. The window opens on the last stored day on or before its start date; the flow of that opening day is already inside its value.

A window spanning at least a year is also shown as an annual rate. A shorter one is never annualised.

The benchmark's return is its close at the end of the window over its close at the start. Unless the benchmark is set up as a total-return series, its return leaves out dividends, which flatters the portfolio a little; the page says so.

## Thresholds and defaults

| Setting | Value |
| --- | --- |
| Oldest opening day accepted before a window's start | 7 days |
| Annual rate shown from | 1 year |
| Flow timing | start of the day |

## When it cannot be computed

- Fewer than two stored valuation days.
- The stored history starts after the window's start: the window is unavailable and says when the history starts, rather than showing a shorter period under the longer label.
- No value is stored within 7 days before the window's start.
- A subperiod whose opening value (or opening value plus flow) is zero or negative has no base and is skipped; the figure says how many were skipped.
- Too large a share of the portfolio's value has a missing price, flow or corporate action: the figure is withheld rather than shown wrong.

## Limits

- Values are daily. A deposit invested late in the day still counts as invested for the whole day.
- The time-weighted return is not the owner's own return on the money put in. That is the money-weighted return (XIRR), shown beside it, which counts the timing of each deposit.
- A benchmark quoted in another currency is compared without converting it, and the page notes when the currency differs from the base currency.

## Golden examples

Test files: `apps/backend/tests/test_performance.py` and `apps/backend/tests/test_analytics_golden.py`.

- `test_twr_is_not_distorted_by_a_deposit_but_xirr_reflects_timing`: 1,000 grows to 1,100, then 1,000 is deposited and the 2,100 falls 5 % to 1,995. TWR = 1.1 x 1.0 x 0.95 - 1 = 4.5 %, while the money-weighted return is lower because the deposit arrived just before the fall.
- `test_twr_withheld_when_material_value_is_missing`: with 5 % of the value missing a price, the return is withheld.

## Version

Method `twr` version 2.0.0 (`apps/backend/src/steward/analytics/versions.py`): flows at the start of their day.
