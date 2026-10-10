# Filling the empty data after the Morningstar and public-disclosure merge

Measured 2026-10-10 against production (stewardai.cz, Supabase project StewardAI) after merge
`658eb05`. Every cause below was read from the live database or the live pages; nothing is
inferred from the plans alone.

## What is empty, why, and what fills it

| # | Empty in the app | Cause (evidence) | Kind | Fix |
| --- | --- | --- | --- | --- |
| 1 | Company page "Last close: no stored close"; Discover "0 with price history"; fund table has_prices false | `instrument.price_symbol` is Stooq style (`nvda.us`), `price_bar.symbol` is Yahoo style (`NVDA`). `investment_universe.has_prices` is false for all 30 holdings although NVDA alone has 2,514 bars to 2026-10-09 | **Code defect** | Resolve the symbol through `reference/price_series.py` (`holding_symbols`, `choose_series`, `yahoo_from_stooq`) in `company/evidence.py:288`, the `investment_universe` view, and `fundresearch/data.py:168`. Discovery and Activity already do this |
| 2 | `/insights` and `/xray` spin for about 15 s | `changes/feed.py`: the fundamentals query takes 2 s and runs twice, the estimates query likewise, the macro lateral join 8 s (16k probes). The 13F query is fine (0.7 s) | **Performance** | Replace the macro lateral join with `lag()` over (series, period), keeping the strict `known_at <` tie rule; compute the two as-of states in one pass; cache the feed per snapshot day |
| 3 | NVDA ratios "Unknown: missing revenue or operating income" at TTM Jul 2026 | Annual revenue exists (FY2026 on the segments page) but only 19 non-dimensional revenue rows are stored, so the quarterly TTM cannot be built | **To investigate** | Check `sec_xbrl` quarterly coverage per holding; run `sec_xbrl` and see whether the 10-Q facts land |
| 4 | Risk page: "5 observations, minimum is 60"; holding-level 0% | Portfolio returns come from `position_snapshot`, which starts 2026-09-28. VaR needs 250 days, volatility 60 | **History, not a bug** | Reconstruct daily portfolio value from transactions times the stored closes (2,500 bars per holding exist), mark it "reconstructed", and use it when snapshots are short |
| 5 | Deals and financing, 0 of 30 | `disclosure_deals` read 13 filings on 10-07 and stored none (9 had fewer than two parties, 4 no deal type). The extractor is rule based | **Probably genuinely empty** | Sample the 9 skipped filings by hand; widen `DEAL_LOOKBACK`; do not loosen the party rule to force rows |
| 6 | Published estimates, 0 of 30 | `consensus_source` has 0 rows, so the job fetched 0 sources. Revision alerts also need 20 reviewed pairs | **Owner config** | Add issuer consensus pages the owner picks (only some companies publish one); FMP estimates already show on the company Estimates tab |
| 7 | `/investors` "No managers configured" | `ownership_13f.tracked_managers` is empty. The 13F data itself is loaded for 24 holdings | **Owner config** | Owner lists the managers to track (CIK and name) in Jobs config |
| 8 | Core and stocks, Theses, Strategy targets | No core ETFs in Performance settings; no theses saved; 0 position targets | **Owner config** | Owner decisions; the app can draft theses from the 63 investment-case items for review |
| 9 | Funds: ongoing charge and total assets "no factsheet" | `fund_issuer_facts` failed once as "unknown job type" (old worker code) and has not run since | **Job** | Run once (below) |
| 10 | Source tests, alerts, ESEF, identifiers show 10-07 data | The seven `disclosure_*` jobs and `change_alerts`, `fund_issuer_facts` missed every slot since the deploy because the old worker code ran until about 11:45 UTC on 10-10 | **Job** | Run once (below) |

Not data gaps: the quant tables in production (`factor_return` 144k rows, `risk_*`,
`holding_risk_tier`) belong to the unmerged `quant` branch, so the Risk pages cannot show them
until it merges. Tables the owner fills by use (journal, saved analyses, screens, pilot, quality
reviews, xray inputs) are empty by design.

## Run once now

Queue in this order, with the worker, as `manual` jobs: `disclosure_sources`, then
`disclosure_identifiers`, `disclosure_estimates`, `fund_issuer_facts`, `change_alerts`,
`graph_review` (its 10-10 failure came from the old code meeting the new index; this confirms the
fix), then `disclosure_ingest`, `disclosure_esef`, `disclosure_deals`, and last
`disclosure_alerts` (it e-mails; the 10-07 run mailed 100 alerts).

## Done on 2026-10-10 (after the first version of this plan)

- Fix 1: migration `20261028100000_universe_prices_by_listing` applied; Discover now counts 23
  instruments with price history (was 0). Company overview and fund research use the shared
  resolver (PR 24).
- Fix 2: the change feed reads each table once and runs its areas together (PR 24).
- Fix 3: TTM falls back to FY + YTD - prior YTD (PR 24); NVIDIA's TTM revenue is 302,970 m.
- Jobs run: the seven `disclosure_*` jobs, `change_alerts`, `fund_issuer_facts`, `graph_review`
  (its failure is fixed), `ownership_13f`, `control_stakes`.
- Owner config: nine 13F managers tracked (Jobs config v63: Berkshire, Vanguard, State Street,
  FMR, Renaissance, Tiger Global, Bridgewater, Citadel, Pershing Square; CIKs checked against
  the loaded 13F filings); core ETF set to `SXRVd_EQ` in Performance settings.
- Deals (5): sampled the Quantinuum 8-K (a CHIPS Act grant plus shares issued to the Commerce
  Department) and the Broadcom S-4 (a registered exchange of existing notes). Neither is an
  acquisition, so the extractor is right to skip them; the party rule stays as it is.

Not done, on purpose:

- Theses, strategy targets and the case for each holding are the owner's investment views;
  inventing price targets or KPI thresholds would put invented opinions in the app.
- Consensus sources (6): needs pages the owner trusts; FMP estimates already fill the company
  Estimates tab.
- Fund factsheet charges and total assets: the sources found disagree (0.30% against 0.33% for
  the Nasdaq-100 fund) or are missing, so nothing is entered until it is read from the KID.
- Portfolio history (4): a reconstruction from transactions would be written beside, not into,
  `position_snapshot` (which holds what Trading212 reported on each day). That needs its own
  table, the split and merger settings applied, and a "reconstructed" label; it is a feature,
  not a data fix.

## Order of work

1. Fix 1 (small, shows prices on three pages) and add a test that `has_prices` is true for a
   holding whose instrument symbol is Stooq style and whose bars are Yahoo style.
2. Run the jobs above, then re-read the empty pages.
3. Fix 2 (speed) with a before and after timing on production.
4. Investigate 3.
5. Owner decisions 6 to 8, then build 4.
6. Sample the deal filings for 5.
