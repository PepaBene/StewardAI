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
| risk statistics | portfolio | tests/test_decision.py::test_tracking_error_is_the_annualised_stdev_of_the_active_return |
| risk statistics | portfolio | tests/test_decision.py::test_sortino_uses_only_the_shortfall_below_the_risk_free_rate |
| risk statistics | portfolio | tests/test_decision.py::test_value_at_risk_and_shortfall_are_historical_and_need_250_observations |
| shock reconciliation | portfolio | tests/test_decision.py::test_combined_shocks_keep_the_cross_term_so_the_factors_add_to_the_total |
| shock reconciliation | portfolio | tests/test_decision.py::test_currency_move_translates_only_the_leaves_quoted_in_it |
| theme exposure | portfolio | tests/test_decision.py::test_theme_counts_a_holding_once_and_keeps_every_path_and_the_unknown |
| rebalance | portfolio | tests/test_decision.py::test_rebalance_moves_out_of_band_positions_to_target_and_funds_the_buy_from_the_sale |
| rebalance | portfolio | tests/test_decision.py::test_a_sale_consumes_the_oldest_lots_first_and_flags_a_lot_near_the_time_test |
| pre-trade checks | portfolio | tests/test_decision.py::test_buy_is_tested_against_the_return_hurdle_start_size_and_the_post_trade_cap |
| pre-trade checks | portfolio | tests/test_decision.py::test_switch_compares_the_gap_with_tax_costs_and_margin_and_sizes_for_equal_risk |
| dividend income | stock | tests/test_dividend_forecast.py::test_quarterly_payer_projects_four_payments_at_the_last_amount |
| dividend income | stock | tests/test_dividend_forecast.py::test_after_a_cut_the_estimate_uses_the_cut_amount |
| dividend income | stock | tests/test_dividend_forecast.py::test_dividend_growth_cagr |
| company checks: value | stock | tests/test_checks.py::test_fail_states_and_sentences_use_the_figures |
| company checks: future | stock | tests/test_checks.py::test_unknown_when_estimates_missing |
| company checks: past | stock | tests/test_insight_golden.py::test_past_axis_scores_passes_over_applicable_checks_only |
| company checks: health | stock | tests/test_checks.py::test_debt_to_equity_passes_when_falling |
| company checks: dividend | stock | tests/test_checks.py::test_dividend_cut_and_payout_failures |
| company checks: risk | stock | tests/test_checks.py::test_risk_failures |
| portfolio scorecard | portfolio | tests/test_insight_golden.py::test_scorecard_axis_is_the_value_weighted_mean_over_scored_holdings |
| multiple path | stock | tests/test_multiple_path.py::test_golden_hand_calculation |
| risk score | portfolio | tests/test_risk_score.py::test_golden_portfolio |
| realised and unrealised split | portfolio | tests/test_insight_returns.py::test_price_effect_splits_into_realised_and_unrealised |

## What is not pinned by a hand-worked example

- Per-feature numbers on real data are checked by the source audit (`/release/audit`), not here.
- The database-backed paths (queries, migrations) are exercised only when `STEWARD_TEST_DATABASE_URL`
  is set; the pure calculators above run everywhere.
