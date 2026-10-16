# Insight coverage audit

Written **2026-10-16** for insight plan phase I0.1. This page states what the dividend engine, the
rule checks, the fair-value features and the analyst-consensus features can honestly claim for each
holding, and how that is measured. The per-holding result is not copied here: it changes with
every sync, so it is computed live and shown on **Data quality → Insight coverage**
(`GET /quality/insight-coverage`, module `steward/ops/insight_coverage.py`, method version
`insight_coverage` in `steward/analytics/versions.py`).

## States

Every cell is one holding against one area and carries a reason sentence.

| State | Meaning |
| --- | --- |
| `ready` | The stored data supports the feature for this holding as designed |
| `partial` | Some data exists; the feature runs and labels what is missing |
| `unavailable` | No usable data; the feature shows the reason, never a zero or a guess |

A feature reading a `partial` or `unavailable` cell must say so on screen (§3 of the plan: an
unknown never scores, absence is never zero).

## Areas and rules

| Area | `ready` when | `partial` when | Source rows |
| --- | --- | --- | --- |
| Dividend history | confirmed `cash_dividend` events span at least 3 calendar years | any event exists (suggested or confirmed) | `corporate_action` |
| Pay dates | every stored event has a payment date | some do | `corporate_action.payment_date` |
| Received matched | every Trading212 dividend received matches a declared event | at least one matches | reconciliation in `steward/dividends/reconcile.py` |
| Filed inputs | all of `dividends_paid`, `dividends_per_share`, `weighted_shares_diluted`, `interest_expense`, `cash_from_operations`, `net_income`, `total_liabilities` have an FY fact | some do | `financial_statement_fact` |
| Estimates | at least two future-period FMP consensus rows | one row | `forward_observation` (`published_analyst_consensus`) |
| Price target | latest `consensus_snapshot` has `access_state = complete` | `partial` | `consensus_snapshot` |
| Peer medians | all four peer-relative checks resolve (pass, fail or n/a) | at least one does | latest `company_check_snapshot` |
| Fair value | a `dcf_base`, `multiple_base` or `thesis_target` point was recorded in the last 7 days | any basis was | `fair_value_point` |

The peer rule uses `min_peers_for_median` from `steward/analytics/thresholds.py` (5): a median is
never drawn from fewer peers; the check is `unknown` and says "needs 5 peers, has 3".

## What is known before the first live run

- **Estimates.** The 2026-10-07 data report found FMP estimates for 13 of 24 eligible holdings; the
  rest are `unavailable` and every forward check on them is `unknown`.
- **Foreign dividend payers** (CSG, Chugai, Daiichi Sankyo, Nu, ASML) are expected to be `partial`
  for dividend history: Twelve Data rows arrive `suggested` and the Yahoo second source is the
  corroboration path (see the access verdicts below). Estimates of the next payment are made only
  where at least `min_payments_for_estimate` (4) payments exist.
- **Price targets.** Nothing is assumed accessible; the consensus chain records the access state of
  each attempt (`paid_only`, `access_unverified`, `not_covered`) and the screens show
  "unavailable" until a `complete` snapshot exists.

## Re-running

The audit reads stored rows only and makes no provider call. It runs on every request to the
endpoint. To change what the cells can show, run the source jobs: `twelve_data_corporate_actions`,
`yahoo_dividends`, `dividend_forecast`, `consensus_snapshot`, `fair_value_snapshot`, and the
`insight_access_probe` for endpoint verdicts.
