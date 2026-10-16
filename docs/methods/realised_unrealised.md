# Realised and unrealised gains

The price effect of a holding is split into the part earned on units sold in the period (realised) and the part on units still held (unrealised), and each holding gets a position record with an annual rate.

## What it shows

In the return attribution, each holding's price effect is divided into realised and unrealised. In the positions view, each holding is open or closed, with its transactions, invested amount, sale proceeds, dividends, fees, realised gain, unrealised gain, total gain and an annualised return.

## Inputs

- Daily account valuations and the position snapshot per day: quantity, price in the listing currency, rate to the base currency, value.
- Stored fills (signed quantity and price), dividends and fees.
- For positions: the classified ledger in the account currency, the owner's entered splits, the realised gains from the lot book, and the latest position snapshot for open positions (quantity, value, cost, date).

## Method

**Attribution over each pair of consecutive valuation days**, per holding, in the base currency:

```
price effect = (q1 * p1 - q0 * p0 - sum(fill quantity * fill price)) * fx1
FX effect    = q0 * p0 * (fx1 - fx0)
```

Here q0, p0 and fx0 are the opening quantity, price and rate, q1, p1 and fx1 the closing ones. Prices are in the listing currency. The window total is the sum of the daily intervals. The bridge is:

```
gain = end value - start value - external flows
residual = gain - (price + income + FX + fees)
```

The residual is shown, never folded in.

**Realised part of the price effect.** Sales in the interval are matched first to the opening units, at the opening price, then to units bought in the interval, at the interval's average buy price:

```
realised = fx1 * ( sum over sold opening units of  units * (sale price - opening price)
                 + sum over sold units bought in the interval of  units * (sale price - average buy price) )
unrealised = price effect - realised
```

**Unattributed holdings.** When the change in quantity in an interval is not explained by stored fills (more than 0.000001 units off), the holding is left out of price and FX for that interval and reported as unattributed, and the bridge is marked partial. A holding bought and sold within one interval needs a stored rate, else it is also unattributed.

**Positions.** A position is closed when the ledger's running quantity is zero (within 0.000001) and the snapshot reports no units. Otherwise it is open.

```
invested = cash paid for buys, fees included
proceeds = cash received for sales
unrealised gain = snapshot value - snapshot cost            (open positions)
total gain = realised gain + unrealised gain + dividends    (closed: unrealised is zero)
```

A split row restates earlier units without cash, so the running quantity stays in today's units. Entries that need an exchange rate the ledger does not carry are left out and the position says so.

**Annual rate.** The annualised money-weighted return (XIRR) uses the holding's own dated cash flows plus, for an open position, its ending value on the snapshot date. Days are counted actual over 365. The solver uses bisection between -99.99 % and +10000 %.

```
days held = end date - first trade date      (end = snapshot date if open, else last trade)
no annual rate when days held < 30
```

## Thresholds and defaults

| Setting | Value |
| --- | --- |
| Quantity tolerance | 0.000001 units |
| Minimum days held for an annual rate | 30 |
| XIRR day count | actual over 365 |
| XIRR bounds | -99.99 % to +10000 % |

## When it cannot be computed

- Attribution needs at least two valuation days.
- A holding with unexplained quantity changes is unattributed (a split, a missing fill).
- Positions held under 30 days have no annual rate ("held N days; an annual rate needs 30").
- No cash flows stored: no annual rate.
- The rate is unavailable, with the reason, when the flows have no outflow or no inflow, all fall on one date, or no single stable rate fits.
- Total gain is empty for an open position without a snapshot value.
- A warning shows when the ledger's units differ from the broker's, or when the ledger holds units the snapshot does not report.

## Limits

- The split uses daily snapshots. Several trades in one interval are all valued against the opening and closing prices of that interval.
- Realised gains in attribution use opening price and average buy price, not tax lots. The lot book gives the realised gain for positions.
- Interest on cash, cash translation between the account and base currency, and broker rounding fall into the residual.
- A short holding period makes an annual rate misleading, which is why none is shown under 30 days.

## Golden examples

Test file: `apps/backend/tests/test_insight_returns.py`.

- `test_price_effect_splits_into_realised_and_unrealised`: 10 units at 100, 4 sold at 110 during the day, 6 left closing at 105. Price effect = 6 x 105 - 10 x 100 + 4 x 110 = 70. Realised = 4 x (110 - 100) = 40. Unrealised = 6 x 5 = 30.
- `test_units_bought_and_sold_in_one_interval_use_the_buy_price`: 5 bought at 50 and sold at 56 between snapshots. Realised = 5 x (56 - 50) = 30, nothing open.
- `test_closed_position_lists_its_rows_with_a_split_and_an_annual_rate`: 20 units bought for 1000, a 2:1 split on 2024-03-01, a dividend of 20, then all 20 sold for 1200 on 2025-01-02. The position is closed. Invested 1000, proceeds 1200, dividends 20, realised gain 200, total gain 220. Flows are -1000, +20 and +1200 over 366 days, and the annual rate is about 22 % (0.2196, tested within 0.002).
- `test_open_position_adds_todays_value_and_short_holds_get_no_annual_rate`: 10 units bought for 100 on 2025-12-20, value 110 on 2026-01-01. Unrealised gain 10, total gain 10, and no annual rate because it was held 12 days.

## Version

Method `attribution_split` version 1.0.0 and method `positions` version 1.0.0 (`apps/backend/src/steward/analytics/versions.py`). The attribution bridge itself is method `attribution` version 1.0.0 and the annual rate is method `xirr` version 1.0.0.
