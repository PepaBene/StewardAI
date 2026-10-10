# Feature inventory: existing code against the Morningstar-style plan

Plan task `M0.1`. Status per feature: **reuse** (use as is), **extend** (add to the existing calculator or page), **new**. Rule: one named owner and method per figure, no second truth store. Existing user feedback and config versioning are preserved. Paths are under `apps/backend/src/` unless noted.

| Feature | Existing owner | Status | Notes / duplication risk |
| --- | --- | --- | --- |
| Shared result envelope, rounding, method versions | none (floats and ad-hoc models) | **new** — `steward/analytics/contract.py`, `versions.py` | New code uses it; legacy models migrate when touched. |
| Security price return | `reference/risk.py` `period_return` (float, last close on or before start); `price_return_series` table | **extend** — canonical Decimal rules in `analytics/returns.py` | Stored `price_return_series` stays the source of record; the new calculator is the specification and test oracle. No second store. |
| Total return, splits, dividends | `reference/returns.py`, `price_return_series`, corporate-action tables | **reuse** engine, **extend** with `analytics/returns.total_return` fixtures | Reinvestment convention now documented. |
| Portfolio TWR | `portfolio/history.py` `flow_adjusted_index` (same end-of-day flow convention) | **reuse**; `analytics.returns.twr` is the Decimal reference with coverage gating | Phase 4 (`M4.3`) moves `portfolio/read.py` onto it. |
| Money-weighted return / XIRR | none | **new** — `analytics/returns.xirr` | Phase 4 consumer. |
| Contribution / attribution | `portfolio/read.py` `contribution_to_return`, `history.position_organic_gain` | **extend** — `attribution_bridge` adds the disclosed residual | Continue the existing waterfall UI. |
| Volatility, drawdown, beta, correlation | `reference/risk.py` (floats), `portfolio/risk.py` (Sharpe, beta, drawdown) | **reuse**; `analytics/risk.py` adds downside deviation, VaR/ES and minimum-sample gates | Existing stay until Phase 6 migrates. |
| Fund look-through and exposure | `lookthrough/read.py` `compute_look_through`, `funds/aggregate.py` | **reuse** (production); `analytics/exposure.look_through` is the independent specification | Same rule: unknown never redistributed. |
| Fund overlap | `funds/aggregate.py` `compute_fund_overlap` (sum of min) | **reuse**; golden cases pin the rule, add the "missing holdings is not zero" case | |
| Concentration (HHI, effective N, top 10) | `funds/aggregate.py`, `lookthrough/read.py` `_concentration` | **reuse**; `analytics.exposure.concentration` pinned by golden cases | |
| Portfolio P/E and valuation aggregate | `valuation/read.py` `aggregate`, `portfolio_valuation` | **reuse**; `portfolio_earnings_yield` pins the earnings-yield rule | |
| Holding valuation, price/ratios | `valuation/read.py` `holding_valuation` | **reuse** | DCF/reverse DCF are **new** (`analytics/valuation.py`, Phase 2). |
| Peer metrics (six filed ratios) | `peer_metrics.py` | **reuse**, **extend** via `analytics/ratios.py` | Single definition rule: ratios merge into one library in `M2.15`. |
| Screen (six metrics, three universes) | `screen.py`, `api/screen_routes.py`, `/screens` page | **extend** (Phase 1) | `/screens` stays; `/discover` is the research-cluster page and links to it. |
| Watchlist CRUD | `api/watchlist_routes.py`, `/watchlist` | **reuse** | Discover adds to it through the same table. |
| Universe / listings / identity | `security`, `security_listing`, `instrument_security_link`, `listing_lifecycle_snapshot` | **extend** (Phase 1: `investment_universe` read model and snapshots) | |
| Fund profile, factsheet, expense ratio | `fund_factsheet`, `lookthrough/factsheet.py` | **reuse** | Fund screens read it; unknown fees stay null. |
| ETF snapshots and holdings | `etf_snapshot`, `etf_holding` | **reuse** | |
| Insider / PDMR classes | `ownership/insider_classify.py`, `insider_read.py` | **reuse** for the cluster flag (`M1.8`) | |
| Pages: `/` overview, `/holdings`, `/holdings/[ticker]`, `/watchlist`, `/funds`, `/screens` | `apps/web/src/app/(app)/` | **reuse**; link to new views | Preserved entry points. |
| Discover page | none | **new** — `/discover` (Phase 1) | |
| Saved screens, peer overrides | none | **new** (Phase 1) | |
| Scenarios, journal, data-quality cockpit | none | **new** (later phases) | |

## Duplicate-truth check

- Returns: stored series (`price_return_series`) and `portfolio/history.py` are the production sources; `analytics/` holds the reference rules and oracles. No parallel table.
- Peers: existing `peer_set` table (suggested/confirmed/rejected) is the store; Phase 1 baselines read it and add no new membership table.
- Universe: derived from existing identity tables plus a snapshot table for point-in-time membership; it does not copy listing facts.
