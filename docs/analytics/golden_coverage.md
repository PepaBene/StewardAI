# Golden-example coverage (plan M8.1)

Each calculation family the plan names is pinned by at least one test whose expected figures were
worked out by hand, not read from the code under test. This file maps the family to those tests.
`apps/backend/tests/test_golden_map.py` fails if a mapped test is renamed or removed, or if a
family loses its row, so the map cannot drift from the suite.

Format of a row: `| family | scope | file::test |`, one test per row. Add a row when you add a test.

| Family | Scope | Test |
| --- | --- | --- |
| accounting identity | stock | tests/test_company_statements.py::test_balance_sheet_balances_and_other_is_the_residual |
| accounting identity | stock | tests/test_company_statements.py::test_an_unbalanced_period_fails_the_check |
| return reconciliation | portfolio | tests/test_analytics_golden.py::test_attribution_reconciles_with_disclosed_residual |
| return reconciliation | portfolio | tests/test_analytics_golden.py::test_twr |
| return reconciliation | stock | tests/test_analytics_golden.py::test_total_return |
| x-ray bucket totals | portfolio | tests/test_xray.py::test_leaves_add_up_to_the_whole_portfolio_and_match_the_aggregate |
| x-ray bucket totals | portfolio | tests/test_xray.py::test_every_view_adds_up_to_the_whole_portfolio |
| x-ray bucket totals | fund | tests/test_xray.py::test_views_reconcile_when_a_fund_reports_more_than_100_percent |
| valuation sensitivity | stock | tests/test_company_valuation.py::test_sensitivity_grid_centre_equals_the_scenario |
| valuation sensitivity | stock | tests/test_analytics_golden.py::test_dcf_and_reverse_dcf |
| fx inversion | portfolio | tests/test_fx.py::test_parse_ecb_inverts_to_eur_per_unit |
| fx inversion | portfolio | tests/test_fx.py::test_ecb_cross_rate_matches_manual_calculation |
| peer-universe membership | stock | tests/test_discovery.py::test_delisted_member_stays_in_past_universe_and_leaves_the_current_one |
| peer-universe membership | stock | tests/test_discovery.py::test_every_universe_member_is_accounted_for_and_unknown_is_not_zero |
| point-in-time screen | stock | tests/test_analytics_golden.py::test_point_in_time_restatement_and_macro_revision |
| point-in-time screen | stock | tests/test_analytics_golden.py::test_delisted_names_stay_in_past_universes |
| fund look-through | fund | tests/test_analytics_golden.py::test_look_through_keeps_unknown_and_paths |
| fund overlap | fund | tests/test_analytics_golden.py::test_overlap |

## What is not pinned by a hand-worked example

- Per-feature numbers on real data are checked by the source audit (`/release/audit`), not here.
- The database-backed paths (queries, migrations) are exercised only when `STEWARD_TEST_DATABASE_URL`
  is set; the pure calculators above run everywhere.
