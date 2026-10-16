# Dividend income

Forward dividend income is the next twelve months of dividends per holding, split into declared, estimated and unconfirmed rows.

## What it shows

For each holding it lists the dividends expected in the next 365 days, converted to the account currency, with totals for declared and estimated income. Unconfirmed rows are listed but never counted in a total.

Related income figures are computed from the same schedule: income received in the last 12 months, yield on price, yield on cost, dividend growth, years without a cut, and a forecast accuracy check.

## Inputs

- Confirmed dividend events (corporate actions of type cash dividend): ex-date, pay date, amount per share, currency.
- Suggested events. A suggested event counts as history only when a second, independent source agrees within 2 % of its amount.
- Trading212 dividend receipts not matched to a confirmed event (gross per share as reported).
- Today's share count per holding.
- The latest stored exchange rate from the dividend currency to the account currency.
- The holding's 5-year dividend growth (used only when "assume growth" is on).
- The holding's observed withholding share, when matched payments show one.

## Method

**Declared and unconfirmed rows.** An event in the window (ex-date or pay date after today, pay date within 365 days) becomes a row. A confirmed event is `declared`. A suggested event is `unconfirmed` and stays out of every total.

**History.** Past payments come from confirmed events, from receipts not already covered by an event, and from corroborated suggested events. Two points less than 4 days apart count as one payment, and the event point wins over the receipt.

**Special payments.** A payment flagged special by its source is special. With at least 3 non-special amounts, a payment above twice their median is also special. Specials are listed apart and never used for cadence or estimates.

**Cadence.** Regular payments of the last 3 years plus 30 days are used (all regular payments if none fall in that window). The median gap between consecutive pay dates decides the cadence:

```
monthly      median gap 20 to 45 days
quarterly    median gap 70 to 115 days
semi_annual  median gap 150 to 215 days
annual       median gap 300 to 430 days
```

The cadence is `irregular` when fewer than 60 % of the gaps lie within 35 % of the median gap, or when the median falls outside every band. Fewer than two payments gives `unknown`.

**Estimated rows.** The step is 365 divided by payments per year (12, 4, 2, 1). Starting from the last regular payment, payment k falls at:

```
pay date = last pay date + round(step * k) days, for k = 1, 2, ... until past today + 365 days
amount per share = last regular amount per share
```

With "assume growth" on, the amount is grown by the 5-year dividend growth:

```
amount = last amount * (1 + growth) ^ (days since last payment / 365)
```

The ex-date is the pay date minus the median ex-to-pay lag of the regular history. Without a lag on record the ex-date is not estimated. A slot is skipped when a declared or unconfirmed row lies within half a step of it.

**Amounts in the account currency.**

```
gross = amount per share * share count * latest rate
net = gross * (1 - withholding share)      only when a withholding share is known
```

The rate and its date are stated on each row. A row without a stored rate has no amount and is flagged in its note.

**Totals.** Declared, estimated and unconfirmed gross totals are summed separately. Rows without an amount are left out.

**Related figures.**

- Received in the last 12 months: the Trading212 dividend rows of the last 365 days, in the account currency.
- Annual dividend per share: regular payments summed by calendar year of the pay date. Mixed currencies give no result.
- Dividend growth: compound growth of calendar-year dividends per share between last calendar year and the year 5 (or 10) years before it, when every year between has a payment and both ends are positive.
- Years without a cut: consecutive complete calendar years, ending last year, in which dividends per share did not fall by more than 1 %.
- Forecast accuracy: a stored forecast at least a year old is compared with payments in the 365 days after it. A forecast row is a hit when a payment of the same holding lands within 21 days of its pay date. The hit rate is hits over rows. The error of a hit is `(forecast per share - received gross per share) / received gross per share`, averaged.

## Thresholds and defaults

| Setting | Default | Where |
| --- | --- | --- |
| Forecast horizon | 365 days | code constant |
| History used for cadence | 3 years plus 30 days | code constant |
| Special payment | above 2 times the median | code constant |
| Share of gaps near the median | at least 60 %, within 35 % | code constant |
| Minimum regular payments for an estimate | 4 (editable, 2 to 12) | `min_payments_for_estimate` |
| Assume growth | off | `assume_growth` |
| Auto-confirm a suggested event | on | `auto_confirm` |
| Auto-confirm amount tolerance | 35 % | `auto_confirm_tolerance` |
| Payment match window | 21 days | `match_window_days` |
| Cut tolerance | 1 % | code constant |
| Accuracy match window | 21 days | code default |

## When it cannot be computed

- No dividend on record for the holding: status `unavailable`, reason "no dividend on record for this holding".
- Fewer regular payments than the minimum: reason "no schedule, history too short", never zero. Declared rows still show, and the status is `partial` when any exist.
- Cadence `irregular` or `unknown`: status `partial`, payments are not estimated.
- No stored exchange rate: the row has no amount and the status is `partial`.
- Forecast accuracy is hidden until a year after the first stored forecast snapshot, with the date it becomes available.
- Dividend growth needs payments in the start and end years and every year between.

## Limits

- Estimates repeat the last regular amount. They do not read company announcements or earnings.
- Cadence comes from past pay dates only. A changed schedule shows up after several payments.
- Amounts use today's share count and the latest rate, not the share count or rate on the pay date.
- Net amounts use the withholding share seen in matched payments, not tax law.

## Golden examples

Test file: `apps/backend/tests/test_dividend_forecast.py`. Today is 2026-01-01, 10 shares, rate 0.9 EUR per USD, minimum 4 payments.

- `test_quarterly_payer_projects_four_payments_at_the_last_amount`: payments of 0.50 USD on 2025-01-15, 04-15, 07-15 and 10-15 give gaps 90, 91, 92 and median 91, so quarterly. Step is 91.25 days, giving pay dates 2026-01-14, 2026-04-15, 2026-07-16 and 2026-10-15. Each row is 0.50 x 10 x 0.9 = 4.50 EUR, total 18.00. The first ex-date is 2025-12-15.
- `test_a_declared_payment_takes_the_estimated_slot`: a confirmed event of 0.55 USD paying 2026-01-20 is within 45 days of the 2026-01-14 slot, so that slot is not estimated. Declared total 4.95, estimated total 13.50.
- `test_annual_payer_projects_one_payment`: four annual payments of 1.20 EUR, last on 2025-05-12, give one row on 2026-05-12 of 12.00 EUR.
- `test_after_a_cut_the_estimate_uses_the_cut_amount`: the last payment 0.25 gives 0.25 x 10 x 0.9 = 2.25 EUR per row, four rows 9.00. Annual dividends are 2.00 for 2024 and 1.75 for 2025, so zero years without a cut.
- `test_special_dividend_is_listed_apart_and_never_estimated`: five amounts have median 0.50, so the 2.00 payment is special. Four regular payments remain and every estimate is 0.50.
- `test_new_payer_has_no_schedule_rather_than_zero`: two regular payments give status `unavailable` with "history too short: 2 regular payments".
- `test_non_payer_is_unavailable_with_its_reason`: no payments give frequency `none`.
- `test_growth_is_applied_only_when_the_owner_turns_it_on`: with growth 5 % and "assume growth" on, the first amount is 0.50 x 1.05^(91/365) = 0.506119. With it off, 0.50.
- `test_irregular_cadence_is_not_estimated`: gaps 20, 200, 90 have median 90 and only one gap within 35 %, so irregular. Gaps 20, 200, 30 have median 30 and two gaps within 35 %, so monthly.
- `test_dividend_growth_cagr`: 1.00 in 2020 to 1.61051 in 2025 is 10 % a year, and five years without a cut.

## Version

Method `dividend_income` version 1.0.0 (`apps/backend/src/steward/analytics/versions.py`).
