# StewardAI: Morningstar-style and additional analytics implementation plan

Research snapshot: 29 September 2026. **Runs alongside** (from 5 October 2026): phases `Q0`, `Q1`, `Q2.1` and `Q8` of the [quant and risk plan](quant_risk_implementation_plan.md), which compute the owner's risk-adjusted decision loop (strategy principles 5–9); the rest of that plan follows this one. Prerequisites: [free data sources implementation plan](free_data_sources_implementation_plan.md) and the relevant parts of the [public-disclosure datasets plan](public_disclosure_datasets_implementation_plan.md). This is for the owner's personal, single-user app and starts **after** its data, free-access and coverage checks are implemented. The owner has confirmed permission for the intended use; licensing is not a feature blocker.

## Product goal and honest scope

Build an investor workbench for company analysis, stock analysis and portfolio decisions: searchable securities, repeatable financial comparisons, transparent valuation, fund analysis, portfolio X-Ray-style exposure, overlap, performance, risk and evidence-linked research. Extend StewardAI's existing thesis tracking, Trading212 portfolio, SEC/ESEF fundamentals, valuation, ETF look-through, 13F, macro, news and feedback rather than rebuilding them.

Morningstar Investor's relevant workflow includes customizable stock/fund screeners, research pages, personal and total portfolio return, portfolio X-Ray, fund overlap/Stock Intersection, and analyst views. Those workflows can be implemented independently. Morningstar's specific ratings, fair-value estimates, moat ratings, categories, style-box branding and analyst reports are its intellectual property. StewardAI should publish **its own named methods, formulas and assumptions**; no plan task assumes access to Morningstar data.

### Data-dependent feature boundary

| Feature | Feasible with Plan 1 data | Required limitation in the UI |
| --- | --- | --- |
| Held-stock financial history, peers and owner-built valuation | Yes where filed financials and prices pass coverage gates | Show metric/period/source coverage and whether peers are comparable. |
| Portfolio performance, risk and allocations | Yes for transaction/snapshot history that reconciles | Identify incomplete transaction history and unknown corporate actions. |
| ETF look-through, fund overlap and weighted fund ratios | Yes for funds with issuer/N-PORT holdings | Unknown/stale fund weight stays explicit; no claim of complete global coverage. |
| Broad global stock/fund screener | Only over the **measured covered universe** | Display universe count, markets and filter-specific coverage; no “all global stocks” claim. |
| Forward analyst-consensus screens and surprise history | Limited to any free endpoint actually accessible and tested | Distinguish event-calendar estimate, management guidance and true consensus; no synthetic consensus. |
| Historical index constituents, global ownership, full mutual-fund categories | Partial | Use dated issuer/regulatory snapshots or clear “unavailable”; no inferred historical membership. |
| Morningstar star/moat/Medalist ratings or report text | No | Create original StewardAI assessment methods with evidence; do not duplicate proprietary outputs. |

### Engineering rules for every feature

1. Compute financial and portfolio figures in backend code from versioned observations, not in the browser or an LLM. The LLM may explain an already computed figure and must cite the inputs.
2. Return `value`, `unit`, `as_of`, `known_at`, `method_version`, `sources`, `coverage`, and `warnings` for every scored or aggregated output. `null` means unavailable; it is never replaced with zero or a guess.
3. Keep security, issuer, listing, fund and index identities distinct. ADRs, share classes and fund holdings aggregate only at the explicitly chosen level.
4. Retain an original result and methodology version for any saved report or decision. An updated source can produce a new result; it does not rewrite what the owner saw earlier.
5. Treat free-tier endpoint access and quotas as runtime constraints. Display accessible FMP, Twelve Data, UN Comtrade and other data in the owner's app; a paid-only or failed endpoint is an access/coverage gap, not a licensing block.

### Build contract: data model, API and navigation

Use versioned migrations alongside `supabase/migrations/` and extend existing read models wherever possible. The following records are logical contracts; the implementation may split them across narrow relational tables, but must retain their keys and invariants.

| Record | Required fields and behavior | First consumer |
| --- | --- | --- |
| `investment_universe_snapshot` | Security/listing ID, exchange, active interval, asset type, as-of/known-at, qualifying source and inclusion reason. Delisted members remain in historical snapshots. | Screener and historical comparison. |
| `screen_definition` and `screen_run` | Owner, versioned filter JSON, metric definitions, as-of cutoff, universe snapshot ID, result count, excluded/unknown counts and run timestamp. Save both definition and reproducible result metadata. | Saved stock/fund screens. |
| `peer_set` | Issuer IDs, manual/automatic membership, inclusion rule, valid interval and excluded candidates/reasons. A sector label alone never defines a comparable peer group. | Company and sector comparisons. |
| `analytics_result` | Subject, method/version, observation IDs or immutable input hash, value/unit/currency, period, `known_at`, coverage numerator/denominator, warnings and status. | All scored panels and reports. |
| `valuation_scenario` | Issuer/security, owner, forecast years, growth/margin/reinvestment/tax/rate/terminal assumptions, diluted shares, net debt, provenance and saved result version. | Stock workbench, thesis and reverse DCF. |
| `portfolio_ledger_entry` and `portfolio_valuation_day` | Broker source ID, event subtype, settlement/trade dates, security, signed units/cash, currency/FX, fees/taxes, cost-basis link, reconciliation status, position/cash/value and price coverage. Never invent a missing broker event. | Performance, gains and attribution. |
| `fund_exposure_snapshot` | Fund/share-class ID, holdings date, normalized issuer weight, source, cash/derivatives/unknown residual and access state. Preserve original holding identifiers. | Fund compare and X-Ray. |
| `decision_snapshot` | Owner, thesis version, selected scenario, known-at cutoff, cited evidence IDs, method versions, decision and review date. Later data do not rewrite it. | Journal and change feed. |

Add owner-authenticated read routes in `apps/backend/src/api/` for candidate resources: `/discover/stocks`, `/discover/funds`, `/issuers/{id}/analysis`, `/funds/{id}/analysis`, `/portfolio/performance`, `/portfolio/xray`, `/portfolio/scenarios` and `/data/quality`. Add write routes only for saved screens, peer overrides, scenarios and journal entries; keep broker holdings read-only. Responses should expose a common `status` (`complete`, `partial`, `unavailable`, `paid_only`, `disputed`) and an `explain` object with exclusions, formula version, citations and gaps. Use cursor pagination for discovery and bounded result sets for comparison. Update `apps/backend/openapi.json` and typed frontend clients through the existing repository workflow.

Proposed navigation: **Discover** (stock/fund screens and comparison tray), **Companies** (issuer analysis with listing selector), **Funds** (share-class research), **Portfolio** (performance, X-Ray, risk, scenarios), **Research** (reports, thesis and change feed) and **Data quality**. Route naming is an implementation detail; preserve the existing `/holdings/[ticker]`, watchlist, overview and research entry points and link them to the new views. Each chart and table must expose a data date, source link, missing/unknown share and a plain-language formula or definition.

### Calculation rules to write before UI work

These are required method specifications for `M0.2`, with golden examples independent of the production calculator:

| Calculation | Canonical rule | Unavailable/partial behavior |
| --- | --- | --- |
| Security price return | Same listing, quote unit, currency and adjustment basis at both endpoints; `(end / start) - 1`. | Missing endpoint or unresolved split produces no exact return. |
| Security total return | Chain price changes with dated cash distributions and split factors under an explicit reinvestment convention; preserve dividend currency and FX rate used. | Show price return separately when distribution history is incomplete. |
| Portfolio TWR | Break at external flows, value the portfolio immediately before/after each flow or use one documented end-of-day flow convention, then chain subperiod returns: `product(1 + r_i) - 1`. Internal trades/dividends are not external flows. | Do not publish a complete TWR if material flows or prices are missing. |
| Portfolio XIRR | Solve for the annual rate that sets dated owner cash flows plus ending value to zero; store cash-flow sign convention and solver bounds. | If there is no stable root, return `unavailable`, not a fabricated percent. |
| Return attribution | Calculate local price/income, FX, trading/flow and fee effects on a consistent valuation base; include a disclosed residual so components reconcile to total value/return. | Missing actions or stale FX create a partial attribution with the affected market-value share. |
| Fund look-through | For a direct holding use its market value; for a fund holding use `fund market value × underlying weight`. Aggregate by canonical issuer, retaining every path. Cash, derivatives and unmatched weight remain explicit. | Do not redistribute unknown weight across known issuers. |
| Two-fund holdings overlap | After issuer normalization and aligned dates, `sum(min(weight_A,i, weight_B,i))`; show each fund's covered fraction and stale-date warning. | Never report zero overlap where one fund's holdings are unavailable. |
| Portfolio earnings yield and P/E | For valid positive-earnings equities, aggregate `sum(covered portfolio weight × company earnings yield) / covered weight`; invert only a positive, meaningful aggregate. State whether weights include fund look-through. | Negative/missing earnings, cash, derivatives and financial-sector incompatibility stay excluded with coverage; no forced P/E. |
| Exposure concentration | Compute security/issuer/sector/country weights from a single portfolio value date; `HHI = sum(weight_i²)` and effective count `1 / HHI`, with unknown as a separately shown bucket. | No comparison across mismatched as-of dates without a warning. |
| DCF scenario | Discount each owner/model forecast free cash flow plus a terminal value under documented rates; bridge enterprise to equity value with dated net debt, minorities and diluted shares. Reverse DCF solves an input assumption, not a new observed fact. | Missing base cash flow, share count or material balance-sheet item prevents a precise per-share figure. |
| Risk statistics | Use aligned total-return series, explicit frequency/trading calendar, risk-free series, sample size and annualization factor. Historical VaR/expected shortfall are descriptive estimates. | Suppress values when the minimum sample or data completeness threshold in methodology is not met. |

### Interaction and acceptance examples

| User action | Required response | Failure/coverage state to exercise |
| --- | --- | --- |
| Screen “profitable European industrials with low debt” | Result count, exact covered universe, applied fiscal-period definitions, sortable table and why each result passed. | A missing debt metric is `unknown`, not a passing zero; show excluded count. |
| Compare a US ordinary share and its ADR | One issuer analysis with distinct listing prices/currencies and ratio-adjusted comparison. | A ticker collision or missing ADR ratio blocks a false merged price chart. |
| Inspect an ETF holding already owned directly | Combined issuer weight, direct amount, fund-by-fund paths and holdings dates. | Unknown ETF residual does not disappear or get assigned to the direct stock. |
| Ask “why did my portfolio lag?” | Reconciled return bridge, benchmark basis, top contributors, FX/fee effects, period and citations. | If a corporate action or cash flow is missing, give a partial answer and name the gap. |
| Change a DCF growth assumption | Recompute fair-value range and margin of safety immediately from saved scenario inputs; show a sensitivity grid and old/new assumptions. | Missing forward consensus cannot be replaced with a model-generated analyst estimate. |
| Simulate selling an overlapping fund | Proposed weights, X-Ray before/after, costs, cash, unknown exposure and no broker-side mutation. | Insufficient price, lot or fee data produces a partial estimate with visible assumptions. |

### Dependency and release slices

| Slice | Plan 1 gates | Feature plan work | Demonstrable owner workflow |
| --- | --- | --- | --- |
| A: reliable inputs | `D0–D4` | `M0–M2` | Search a covered issuer, compare verified peers and save an original valuation with evidence. |
| B: fund and performance truth | `D3`, `D6`, reconciled Trading212 history | `M3–M5` | Explain portfolio return, a fund's holdings, direct/fund overlap and the unknown share. |
| C: decisions | `D2`, `D5`, all prior analytics | `M6–M7` | Run a macro/FX scenario, inspect a verified change, record a thesis decision and receive a nonduplicative alert. |
| D: release | All source and method checks | `M8` | Reproduce sampled figures and access states from source observation to screen/export. |

Ship each slice behind feature flags for the owner, with old views available until the new result reconciles. A source's absence must not break portfolio sync, and a saved report must remain reproducible even after later revisions.

## Build status and open gaps (7 October 2026, after the gap-closure pass)

What is implemented on the `morningstar` branch and what is **not**. "Built" means the backend rule, API and page exist with tests; it does not mean the page has been used on real data. **No page has been checked in a browser against live data yet.** A gap is listed so it is not mistaken for done; none of them is hidden behind a zero.

### Closed in the gap-closure pass

Golden cases for delisted, renamed, stale-fund and Altman inputs (`M0.4`); backtest engine with frozen universes and trading costs (`M1.7`); provider panel and keyless count (`M1.6`); fund tracking-difference screen metric for funds with a recorded benchmark (`M1.3`); statements and peers in a chosen base currency with ECB or ČNB rates (`M2.2`, `M2.4`, `M2.14`); Altman Z from stored retained earnings (`M2.3`); network "new since" taken from the last thesis review (`M2.8`); segment analysis with eliminations, unallocated revenue, definition changes and an owner mapping to country, commodity, patent and trade contexts (`M2.12`); the decomposable rubric (`M2.13`); the versioned company report (`M2.9`); owner-recorded benchmark symbol, basis and currency, and owner-entered NAV with premium or discount (`M3.4`); saved fund comparisons (`M3.5`); DV01-implied duration, credit-spread sensitivity and owner-recorded maturity, duration and credit notes (`M3.6`).

### Cross-cutting gaps

- **Visual and real-data verification:** the pages passed typecheck, lint and unit tests but were never rendered in a browser, and the database tests use synthetic seed data. Do this before relying on any page. A `.env` was added to the repository folder during the build; it was never read.
- **Saved reports:** the company report saves the code-computed tables, rubric, research excerpt and your conclusion as an append-only version; it does not save a PDF or a model-written narrative of its own. Saved fund comparisons keep the fund list only; figures are recomputed on opening.
- **Generated API clients** (`openapi.json`, `schema.d.ts`) are regenerated by the exporter; on Windows the exporter writes CRLF, so normalise to LF before committing and check the diff has no removed paths or schemas (FastAPI renames both models on a name collision).

### Phase 0 (`4e85415`)

| Item | Status | Gap |
| --- | --- | --- |
| `M0.1`–`M0.3` | Built | Inventory (`docs/analytics/feature_inventory.md`), methodology and the `steward/analytics` package exist. Legacy float calculators were left in place and migrate when touched. |
| `M0.4` | Built | Golden cases cover price/total return, TWR, XIRR, CAGR, attribution, look-through, overlap, concentration, earnings yield, DCF, risk, point-in-time, ADR, peers, delisted names kept in past universes, a renamed fund, a stale fund and Altman Z. Restated filings and revised macro releases are covered in `test_company_statements.py` and the macro tests. |
| `M0.5` | Built | `analytics/thresholds.py`. |

### Phase 1 (`4e85415`)

| Item | Status | Gap |
| --- | --- | --- |
| `M1.1`, `M1.2`, `M1.4`, `M1.5` | Built | Universe view and snapshots, stock predicates, peer baselines, saved screens, CSV export with provenance. |
| `M1.3` | Built, with limits | Tracking difference is a fund-screen metric, filled only for funds where you recorded a benchmark (first 40 per run); others show why it is missing. |
| `M1.6` | Built | Filter builder, coverage, disabled-with-reason filters, a provider panel and a count of filters that need no key. |
| `M1.7` | Built, with limits | Backtest: equal weight, a stated cost on the traded fraction, universe frozen per rebalance, delisted names held at their last price and flagged, unpriced names dropped and counted, survivorship warning. **Metrics that need a provider are not backtestable; sector and industry need an FMP key.** |
| `M1.8` | Built | Composite score, red flags and the idea-pipeline screen. Not re-audited since the commit. |

### Phase 2 (`d9378c4`, `cf14b6f`)

| Item | Status | Gap |
| --- | --- | --- |
| `M2.1` | Built, with limits | Profile, segments, competitors, peers, upcoming events and coverage; revenue by geography, product and segment as the company reports it; legal group (parent, ultimate parent, subsidiaries) with each link marked confirmed or suggested, and a note when the company is an LEI reporting exception. **Sector and industry come only from a stored provider profile; the legal group only from stored entity relationships.** |
| `M2.2`, `M2.14` | Built, with limits | As-reported and standardized statements, derived quarters, restated/original, checks, CSV, and a base-currency view (period-end or average ECB/ČNB rate; a missing rate empties the cell with its reason). **Only line items the statement store keeps are shown; true filing presentation order needs the presentation linkbase, which is not collected. Only single-currency companies are converted.** |
| `M2.3`, `M2.15` | Built, with limits | Ratio library with history, own and peer medians. Altman Z now uses stored retained earnings. **Altman is unavailable for filings collected before retained earnings were stored until they are re-collected. Forward P/E is wired to the FMP consensus (payload field names checked against a live response on 7 October 2026) but shows n/m for any company until the FMP job has run for it; the job collects holdings mapped to a US listing, 20 per run. Organic growth needs acquisition and currency effects the filings do not give in structured form.** |
| `M2.4` | Built, with limits | Peer compare with median, range, fiscal distance, index chart and size rows in a chosen base currency. **The index chart is indexed to each company's own first year.** |
| `M2.5` | Built, with limits | Scenarios, discount-rate build-up, sensitivity grid, reverse DCF, reference multiples. **Assumes no non-controlling interest when none is filed (warning shown).** |
| `M2.6` | Built, with limits | Cited assessment items (moat type, bull/bear, guidance, risk), plus an "Observed in filings" block the app computes itself: largest segment, geography, product and customer share, effective number of segments, cash returned as a share of free cash flow, share count change, net debt issued and free cash flow per share growth. Each states its inputs and shows its reason when empty. **Guidance is typed by the owner, not extracted from filings.** |
| `M2.7` | Built | Estimates tab with access state and metadata. Surprise is suppressed when basis or period differs. |
| `M2.8` | Built | Network and patent tab. "New since" is the last thesis review, with the basis stated. |
| `M2.9` | Built, with limits | Report tab: computed key figures, rubric, revenue mix, the latest verified research with citations, your case items, conflict flags, editable conclusion and append-only versions. **The prose is the stored research report; the report does not write new model prose.** |
| `M2.10` | Partial | Cash-flow-based allocation history and stated actions. Repurchases stated in the notes are reconciled to the cash-flow figure, and stated capital-return actions carry their amounts. Acquisitions and divestitures are the cash the filed cash-flow statement reports for them (and "Other investing" now nets them out); confirmed deal events are listed beside them. **A year collected before these lines were kept shows "not filed" until the filing is re-collected; the FMP deals endpoint is not on the free tier; other announced actions are not reconciled line by line.** |
| `M2.11` | Partial | Dated roles, relationship disclosures, existing insider and 13F components. Named-executive summary compensation for the two newest fiscal years comes from FMP (free endpoint, filing link on each row) once the FMP job has run for the company; it covers holdings mapped to a US listing. Company-wide related-party totals tagged in the filing notes (transactions, revenue, costs, receivable, payable) are shown with their XBRL element. **No counterparty-by-counterparty related-party detail, related-party totals only for filings collected after those concepts were added, and no compensation for companies the FMP job has not collected.** |
| `M2.12` | Built, with limits | Segment, geography and product history with eliminations, unallocated revenue, changed-definition flags and an owner mapping (an estimate, labelled) to country, commodity, patent technology and trade product. **Mapping is manual; no automatic classification.** |
| `M2.13` | Built, with limits | Four-component rubric: banded indicators, stated weights, abstention below 0.6 component coverage, a stated missing-evidence penalty, no stars. **Calibration status is "uncalibrated": thresholds are StewardAI's own and unvalidated against outcomes.** |

### Phase 3 (this branch)

| Item | Status | Gap |
| --- | --- | --- |
| `M3.1` | Built, with limits | `/funds/[isin]` profile with fund/share-class/listing identity. **Replication method, inception, legal domicile, securities lending and tax notes are not in the issuer data**; they are entered by the owner with a source (`fund_owner_fact`). Domicile otherwise shows only the ISIN prefix, labelled as such. |
| `M3.2` | Built | Issuer-normalized holdings, asset-class/sector/country/currency weights, issuer-published breakdowns against the index, unknown weight kept, derived-exposure CSV. Sector and country exist only where the issuer rows publish them. |
| `M3.3` | Partial | Top-10, HHI, effective issuers, size mix and weighted earnings yield, P/B and profitability. **Only the 200 largest issuers are scored; only holdings the app has statements and prices for contribute (the coverage is shown); size bands use ECB rates and fixed EUR thresholds.** A value or growth distribution is shown from StewardAI's own stated rule (earnings yield and price to book votes), with negative-earnings and not-covered weight kept apart; it is not Morningstar's Style Box. |
| `M3.4` | Built, with limits | Listing-price and total return, benchmark (chosen, or the one you recorded with its basis and currency), tracking difference only on a matched basis and currency, volatility, drawdown, tracking error, and premium or discount to NAV you enter with a source. **The `fund_issuer_facts` job (weekdays 08:15) reads the issuer's own published NAV, dated and in the share-class currency, for iShares funds (product-data API) and refreshes replication, launch date, domicile, UCITS status and benchmark index from the iShares API and the WisdomTree factsheet; it writes only changes, never over a value you recorded from another source, and skips what it cannot read. An exchange-traded commodity's factsheet states no NAV, so none is recorded for it; other issuers' NAV remains manual. For iShares funds the same job also records `securities_lending` (yes only, from a current one-year on-loan figure; an old or zero table is left unknown, never "no"), `lending_revenue_share` (the issuer's stated split) and the issuer's benchmark series ("growth of 10,000", source `issuer`, symbol `ishares-bm-<portfolio id>`) with `benchmark_symbol`/`benchmark_currency` facts; `benchmark_basis` and the coverage row's total-return flag are set only when the benchmark's name says total or price return, so the series stays "basis unknown" otherwise. WisdomTree products held in the account are found by instrument name; Vanguard and other issuers are not added because no verified facts endpoint exists for them, and WisdomTree publishes no daily benchmark series or lending data. Requires migration `20261012100000`. There is no automatic fund-to-index link, only an exact name match as a suggestion. Distributing funds assume every recorded distribution is all of them.** |
| `M3.5` | Built | Two to five funds: costs, overlap (sum of the smaller weight) with the shared-holding list, sector, country, concentration, returns, completeness, stale dates; saved comparisons keep the fund list. **Share classes are recognised as one portfolio when they share the issuer's portfolio id, or, marked as inferred, when their identified-equity holdings are the same issuers at the same weights (overlap of 99.5% or more); a class whose weights differ is treated as a different portfolio.** Costs compare the ongoing charge only. |
| `M3.6` | Partial | Duration implied from the N-PORT USD DV01 (US-registered funds only, labelled as reported, withheld if any key-rate bucket is missing), the investment-grade versus non-investment-grade credit-spread sensitivity (not a share of holdings), and owner-recorded average maturity, effective duration and credit-quality note. **Maturity and the rating breakdown are not in any stored data; non-US funds have only what you record. Nothing is inferred from names.** |
| `M3.7` | Built, with limits | Checklist with a stated rule and source per criterion and an explicit abstention when critical inputs are missing. **Thresholds are StewardAI's own and unvalidated against outcomes; securities lending, replication and tax rows stay unknown until the owner records them; no automatic KID or prospectus reading.** |

**Phase 3 exit checks:** fund weights reconcile with residuals (tested); share classes of one portfolio are not double-counted (tested, subject to the detection limit above); price return is never compared with a total-return index without the stated withholding (tested).

### Phase 4 (this branch)

Built on stored daily snapshots and transactions, in `steward/performance/`, behind `/portfolio/performance*` and the `/performance` pages (Returns, Contribution, Lots and holding periods, Core and stocks, Ledger and settings). Owner settings are append-only in `public.performance_setting` (migration `20261013100000`). Methods are in `docs/analytics/methodology.md` section 15. **Not yet checked in a browser or against the live account.**

| Item | Status | Gap |
| --- | --- | --- |
| `M4.1` | Built, with limits | Fills, dividends and cash movements become dated entries with one cash-effect sign convention; deposits, withdrawals and transfers are the only external flows. Reconciled two ways: the change in cash between snapshot days against the ledger, and held units rebuilt from fills against the broker's latest positions. **Only deposit, withdrawal, transfer and fee cash subtypes are read; anything else (for example interest on cash) is listed as unsupported and shows as a cash break. Rows in a currency other than the account currency are listed but not summed.** |
| `M4.2` | Partial | Daily base-currency value is the stored daily snapshot (positions, cash, the day's reference rate), not a series rebuilt from positions and a price history; a day without a snapshot has no value. Price, income, FX and fee components are in `M4.6`. The reconciliation report is the cash and units checks above, with tolerance and a stated likely cause. **No trading-holiday or stale-price handling beyond the snapshot days that exist.** |
| `M4.3` | Built | TWR and XIRR for 1M, YTD, 1Y, 3Y, 5Y and since the first snapshot; annualized only from 365 days; a window the history does not reach is unavailable, not shortened; a precise figure is withheld when cash moved by an amount no transaction explains. |
| `M4.4` | Built, with limits | Lots, FIFO or average cost, realized gain, dividends, fees. Jurisdiction-neutral, no tax liability. **Splits are applied only when the owner enters them (Settings → Splits: ticker, first day of the new count, ratio), which restates earlier fills; mergers, spin-offs and any split not entered still show as position breaks; a sale larger than the stored history is unmatched and has no gain. Withholding tax is not separated from the dividend amount Trading212 reports.** |
| `M4.5` | Built, with limits | Benchmark per window with matched-currency and dividend-basis checks, blended benchmarks with a none, monthly, quarterly or yearly rebalance, active return with its missing share. **The benchmark must be a price series already stored; an owner-typed index series is not built. No active return is drawn when currency or basis cannot be matched.** |
| `M4.6` | Built, with limits | Per-holding price, FX, income and fee effects reconciled to the gain with a disclosed residual; groups by currency, country and the strategy's own classifications. **Fills are taken at the closing rate of their snapshot interval; cash translation, interest and broker rounding stay in the residual. A holding whose units changed without a stored fill is unattributed and makes the bridge partial.** |
| `M4.7` | Built, with limits | Growth of 100 against the benchmark, rolling 90 days, money in and out, dividends and fees by month, fee drag, gain by window, and the ledger-health strip on the returns page. **The returns page shows a freshness badge (prices and rates date, benchmark close date, a warning past 5 days); the other charts do not carry their own.** |
| `M4.8` | Built | Per open lot: buy date, the day it passes the test (owner years, default 3), months left; the year's gross proceeds from lots still inside the test against the owner's limit (default CZK 100,000, converted at the stored rate of each sale date); a flag when the ticker has reached its thesis sell target within the owner's warning months (default 6). Both dates are in the monthly review. Dates and owner thresholds only. |
| `M4.9` | Built, with limits | Core versus stocks, each a rolling time-weighted return after fees (FX conversion fees included), shown in the monthly review's compliance table with the 10-point shift as the proposed review item; "too early to judge" under 365 days. **The core list is the `core_tickers` owner setting, because `Q8.0` (an `exclude` list on the single-position rule) is not built yet; move to it when `Q8.0` lands.** |

**Phase 4 exit checks:** hand-worked fixtures cover mid-period deposits, dividends, cross-currency price and FX, partial sales and lots (`tests/test_performance.py`); TWR is not distorted by a deposit (tested); attribution plus residual equals the gain (tested, including an unattributable holding); a precise return is withheld when the ledger leaves cash unexplained (tested against a database); a partial sale consumes lots oldest first and leaves the remaining lot's date unchanged (tested); the sleeves' weighted returns reconcile to the invested portfolio's (tested). An entered split restates earlier fills and closes the position break (tested, including through the lots report against a database). **Not covered by a fixture: a missing price on a held position (it cannot be stored).**

#### Phase 4 open gaps

Every known gap, so none is mistaken for done. Nothing below is hidden behind a zero; where a figure cannot be produced the page says why.

**Verification not done**
- No Phase 4 page has been rendered in a browser or run against the live account. Migration `20261013100000` has not been applied to the real database. The database tests use a three-day synthetic account.
- Monthly reviews written before this change have no holding-period section. (`build_review` is now tested end to end against a database, including the performance rows.)
- No component or rendering tests for the new pages; only the helper and settings-form logic is unit-tested.
- Fixtures now cover a pence-quoted (GBX) holding, a fill in a currency other than the account's, a position bought and sold between two snapshots and a sale that closes a position. An owner-entered split is now covered; a split the owner has not entered still surfaces as a position break. A price missing on a held position cannot be stored (`current_price` is not null), so that case does not arise.

**Defects found and fixed while closing gaps**
- Core-versus-stocks flows for a sale that closes a position (or a position bought and sold inside one interval) were dropped because the rate was looked up in a position row that did not exist. `sleeve_days` now takes the holding's own rate from its nearest snapshot day (`ticker_fx_lookup`); tested with a closed position.
- Attribution valued the fills of a position bought and sold between two snapshots at a zero rate, so their gain fell into the residual. `attribute` now uses the same per-holding rate; with no rate anywhere the ticker is left unattributed and reported as such rather than valued at zero.

**Assumptions not yet checked against real Trading212 data**
- Cash is taken as `total value - invested value` of the account snapshot; whether that includes cash in pies and reserved cash is assumed.
- Fill prices and snapshot prices are assumed to be in the same unit (including pence for GBX), and the fill quantity sign convention is taken from the side (BUY or SELL), not the stored sign.
- A fill in a currency other than the account's is flagged and left out of every report rather than converted at an unknown rate (tested). Fees reported in a currency other than the fill's are left out of the ledger. A fill's fee basis `unknown` is treated as fees excluded.
- Cash movement subtypes known: deposit, withdraw, transfer, fee. Interest, card and any other subtype is unsupported and shows as a cash break. A dividend's withholding tax is not separated from the amount reported.
- A sell target is compared with the position's price only when the thesis currency equals the quote currency.

**Method limits**
- Daily value is the stored snapshot, so a day without a snapshot has no value and an intraday trade pair is invisible. There is no trading-holiday or stale-price handling beyond the snapshot days that exist (`M4.2`).
- The returns' missing-weight test uses unexplained cash only. A held-unit break does not block TWR (it does not distort it) but does affect lots and attribution.
- Attribution values fills at the closing rate of their snapshot interval; cash translation between account and base currency, interest and rounding stay in the residual. Snapshots in a different base currency than the latest are excluded from every series.
- Lots apply only the corporate actions the owner entered (`splits`, `mergers`, `spin_offs` in the performance settings); none is read from a provider, and one that is not entered still appears as a position break. A split is applied to fills before its first day; a fill on that day is taken as already in the new count. A merger is a share exchange: earlier fills of the old ticker move to the new one (quantity x ratio, price / ratio) with their cost and purchase date, and cash paid for a fraction of a share is not modelled. A spin-off applies at the start of its day: each open parent lot gives the owner's cost share and ratio to a child lot that keeps the parent lot's purchase date (a choice the owner's tax rules must confirm); the cost split comes from the company's notice, which the owner types. Fills on the spin-off day are not entitled to the child shares. Average cost and FIFO are the only methods. A sale beyond the stored history has no gain.
- The exemption total counts only the part of a sale taken from lots still inside the holding period, converted at the stored rate of the sale date, and is a lower bound when a rate is missing. The 3-year rule, the day-after convention and the limit are owner settings from the strategy principle; they have not been checked against the current statute and are not tax advice.
- Benchmark: the series must already be stored (from the price settings); an owner-typed series for an index with no free history is not built. A blend starts on the latest first close of its components. Investable-period coverage is reported as the share of portfolio days with no benchmark close, not as a start-date check. Benchmark-relative attribution compares each holding's gain with its opening value placed in the benchmark (money, in the base currency); a holding bought during the window has no opening money and shows no figure, and it is not a Brinson attribution because the benchmark's weights are not known.
- Attribution groups: currency, country (the instrument's home country) and the strategy's own classifications; no sector or industry from data, and a holding with no label is `Unknown`.
- Core versus stocks: stocks are every held ticker not listed as core, including any ETF the owner forgot to list; the core list is the `core_tickers` setting until `Q8.0` exists, and nothing checks the tickers against the holdings.
- The rolling 90-day chart is fixed; the Core and stocks comparison has a window selector (1, 2, 3 or 5 years, or the saved setting) and attribution has 1M to 3Y. No rolling beta or correlation (those belong to `M6.1`).

**Plan items only partly met**
- `M4.2`: see above. `M4.5`: owner-entered benchmark missing. `M4.7`: the freshness badge (age of prices, rates and benchmark, turning to a warning past 5 days) is on the returns, attribution and Core versus stocks pages and the X-Ray exposure panel; the lots and ledger pages and the other X-Ray tabs do not carry it. The dividend and cash-flow timeline is a table, and the ledger badge is on the returns page and the ledger tab, not on every chart.
- CSV export exists for the ledger, open lots, realized gains and attribution (`GET /portfolio/performance/export`, buttons on those pages). A performance report can be saved under a name (`POST /portfolio/performance/saved`; table `saved_analysis`, migration `20261016100000`): the server stores the report as it stood with its method version and as-of date, and opening it shows those figures, not a recomputation. Saved items are archived, never edited. Only the returns report is saved, not the lots, attribution or sleeves reports.
- Settings are now the config domain `performance` (migration `20261014100000`): config history, diff, rollback, export and import apply, and the old table's newest row is copied in as the first version. They have no entry in the config sidebar; the editor stays on the Performance pages and the history page links to it.
- `M4.9` and `M4.8` now also raise an alert with the monthly review, once a month for a lagging stock sleeve and once per lot, at the owner's `alert_severity` (default 2; 0 turns them off). The default notification rules send severity 3 only, so at 2 the item waits for the digest unless a rule matches it. The alerts are raised after the review is stored, so a failure there never costs the report. There is no escalation if an alert is ignored.

**Operational**
- `lots_report` and `sleeves_report` are recomputed in full on every request and again for every monthly review; `_rolling` is quadratic in the number of days. Fine for a few years of daily snapshots, not cached or indexed.
- The regenerated API contract now includes the earlier `fund_issuer_facts` job; it had been missing from `openapi.json` and the web job form.
- Unrelated and found on the way: 17 existing web files (company, fund and discover views) use `bg-surface-sunken`, a token that does not exist, so those backgrounds have no fill; `xbrl_concepts.py:625` fails the line-length lint; `tests/test_fix_f.py` errors on a Windows temp-folder permission.

### Phase 5 (this branch)

Built in `steward/xray/`, behind `/portfolio/xray*` and the `/xray` pages (Exposure, Overlap, Concentration, Fundamentals, Against a benchmark, What if). Every direct holding and every published fund row becomes a weighted leaf (a share of total portfolio value); cash, a fund's own cash and derivatives, a fund not looked through, and the part of a fund its rows do not account for are leaves too. Each view is a sum of leaves, so a lens reconciles to the whole portfolio by construction. No migration.

| Item | Status | Gap |
| --- | --- | --- |
| `M5.1` | Built, with limits | 16 lenses (asset class, sector, industry, country, domicile, listing country, region, currency, issuer, fund, style, size, fees, revenue country, commodity, trade product); each bucket lists contributors and a reported, inferred, owner or none basis. Current holdings only; industry only from the FMP profile; style and size only for the top 200 issuers. |
| `M5.2` | Built, with limits | Stock Intersection by canonical issuer with the fund-to-underlying trail and each fund's contribution; removing a fund moves proceeds to cash and shows the concentration change. Unknown weight is its own bucket. |
| `M5.3` | Built, with limits | Security, issuer, sector, country and fund levels via the existing concentration function, on identified weight, with unknown weight beside it. Benchmark-relative only where like-for-like. |
| `M5.4` | Built, with limits | Earnings-yield-based P/E, harmonic P/B, dividend yield, profitability, leverage, fee drag; each with coverage, a thin flag under 50%, what was left out and a reason when withheld. |
| `M5.5` | Partial | Revenue geography only from an owner country mapping or reported members named exactly as a country; commodity and trade lenses only from owner mappings. No supply-chain graph. Country kinds are labelled and a mix is warned about. |
| `M5.6` | Built, with limits | Proxy is one configured fund's equity rows renormalised (today's holdings, not historical membership). Sector, country and currency compared only like-for-like, otherwise withheld with a reason. |
| `M5.7` | Built, with limits | Edit weights or add watchlist securities; computed on an in-memory copy, never written (tested against the tables). |

**Phase 5 exit checks:** leaf weights equal the existing aggregate's issuer weights (tested); each lens sums to the whole including cash and unknown (tested); the fund-to-underlying trail survives the issuer merge (tested); mixed country kinds raise a warning and withhold the benchmark country comparison (tested); a proposal never changes saved holdings and over 100% is refused with 422 (tested against a database).

#### Phase 5 open gaps

**Verification not done**
- No `/xray` page has been rendered in a browser or run against the live account. The database tests use synthetic data. `_profiles` is now exercised against stored FMP profile rows (industry, domicile, listing); `_facts` (stored statements, giving a covered profitability figure) and `_revenue` (reported country geography entered through a holding profile) are now exercised against a database (`tests/test_xray_data_db.py`); the owner-mapping path of `_revenue` is not. Fixtures now cover a stale fund, two overlapping funds, a bond row and a pence-quoted holding. Only helper tests plus one server-render test (the composition strip) cover the web side.

**Method limits**
- `M5.1`: today's holdings by default; a date picker above the tabs (the date lives in the address as `?as_of=` and follows you between tabs; What if and Your data ignore it) and `as_of` on every X-Ray read route show the latest daily snapshot on or before that date, with fund holdings fetched and statements known by the end of it. Issuer identity, sector labels, fund charges and the revenue split are today's, and the result says so. Asset class is only equity, cash, derivative or other. Style and size use the app's own value/blend/growth proxy and fixed EUR thresholds for the top 200 issuers. The issuer lens shows 40 plus "all other"; contributors are the top 6. Currency is trading currency, not economic exposure. A non-fund direct holding that is not fund-like is treated as a stock. CSV export exists for one lens or all (`GET /portfolio/xray/export`).
- `M5.2`: the issuer merge relies on config groups, the register and the ISIN. Removal assumes proceeds to cash and is measured on identified issuers. Share-class paths are kept by ISIN and ticker, not by MIC. Unconfigured funds are unknown.
- `M5.3`: HHI is on identified weight. Integrated with `portfolio/risk_limits`: the overlap limit (`max_look_through_weight`) now reads the X-Ray's issuer weights, and two new limits, `max_sector_weight` and `max_country_weight`, read its classified sector and country buckets (unknown weight is never a breach). It falls back to the older look-through when no owner is configured or the X-Ray fails. The older look-through panels (`/funds`, the holdings exposure panel, `/portfolio/look-through`, `/funds/aggregate`) are not merged into the X-Ray, so two parallel UIs still show related figures.
- Owner-entered X-Ray data (table `xray_owner_input`, migration `20261017100000`; tab "Your data"): no provider here supplies regional splits, supply-chain links or index constituents, so each is typed by the owner with a source address and the date it describes. Newest entry per name is in force; entries are archived, never edited. They are labelled as the owner's everywhere and are not official data.
- Automated fill (same table, `origin` and `derived_from` columns): index weights and regional splits are derived from stored fund holdings (`xray/derive.py`) with the fund's issuer data endpoint as source address and the snapshot date; they are the fund's holdings, not the provider's index file. `refresh_all` runs at the end of the look-through job, skips unchanged rows and never overwrites a newer owner entry. Supply-chain links are also read from confirmed `relationship_disclosure` rows (annual-report customers and suppliers, with filing address and fiscal period); an owner link overrides a filing one. Source addresses are the funds' public product pages, opened and matched to their ISINs on 7 Oct 2026 (iShares CSPX 253743; Vanguard FTSE All-World 9679). Freshness badges now cover lots, the ledger and every X-Ray tab (`DataAgeStrip`); the funds page's combined-exposure panel was replaced by a pointer to the X-Ray (the look-through-as-of panel stays because it can pin a snapshot). Remaining limits: `company_graph_edge` is not used; fund rows published without a ticker do not count toward supply-chain weights; a proxy fund's tracking difference needs a recorded benchmark.
- `M5.4`: financials are excluded from profitability and leverage by sector label only, so an unlabelled bank is not excluded. Leverage is shown two ways, net debt to equity and net debt to EBITDA (companies with non-positive EBITDA and financials are left out of the latter and listed). Facts are latest values, not as-of. Top 200 issuers only.
- `M5.5`: revenue geography only for the top 40 issuers plus mapped companies; a reported region such as "Europe" is counted in countries only when the owner has entered a split for that exact name (`POST /portfolio/xray/inputs/regions`); those slices carry the `owner` basis, and a region with no split stays "no data". A supply-chain graph (`GET /portfolio/xray/supply-chain`, tab "Supply chain") is drawn only from links the owner entered; each company is shown against its look-through weight (matched by ticker symbol) and links where both ends are held are listed. No link is inferred, and commodity and trade exposure still come only from owner mappings. Country of risk is unavailable for direct stocks (home market only). Two further lenses keep the meanings apart: domicile (the provider's country when a profile is stored, else the ISIN's country prefix, labelled inferred) and listing country (from the exchange the provider names, through a stated table of exchanges; an unmapped exchange stays unclassified). The listing lens needs a stored profile, so without an FMP key it is empty. The ISIN prefix is where a security was registered, which is not always where the company is based (international securities depositories, some holding companies).
- `M5.6`: one configured fund as proxy. The country comparison is withheld whenever direct holdings (home market) are present, so it is often withheld. Official index weights are not available: the owner can paste an index's constituents with a source and date (`POST /portfolio/xray/inputs/index-weights`) and gets the active share against them (half the summed weight differences on equity, matched by ISIN then ticker symbol; approximate when the pasted weights cover under 97%). Fund-level benchmark-relative is a table of each held fund's annualised tracking difference against the benchmark the owner recorded for it, same basis and currency only, with the weight-averaged figure over the funds that have one (first 40 funds; `GET /portfolio/xray/funds-vs-benchmark`). The proxy fund's own tracking difference appears in that table when the owner recorded a benchmark for it. It is the fund's price or total return against that benchmark, not the portfolio's equity against an index.
- `M5.7`: weights apply at today's values (no price moves, costs, taxes or trades needed). No risk before and after (`M6.1`). A what-if can be saved by name (`POST /portfolio/xray/scenarios`): only the weights are kept, and opening one runs them against the holdings of that day, so it is not a record of an earlier result; a saved ticker no longer held is named and left out. Scenarios have no CSV export. Additions must exist in the universe by exact ticker. All sixteen lenses are compared, except that industry, listing, revenue, commodity and trade-product lenses are left out when no holding carries a label on either side.

**Operational**
- The X-Ray endpoints share one load per owner for 60 seconds (`load_cached`), so a visit loads once, not once per tab; the cost is that a change to holdings or profiles shows up to a minute late. The cache is in process memory (one API worker; each worker would hold its own copy). The proposal endpoint reuses the cached load.
- Reading stored statements for the scored issuers is limited to 25 seconds. On a timeout the X-Ray still loads, without size, style and fundamentals, and says so in its notes.
- The Phase 4 sleeve-flow defect is fixed (see Phase 4 open gaps).

## Phase 0 — Methods, feature inventory and shared calculation layer

**Dependency:** Plan 1 complete and signed off. **Outcome:** precise formulas and a nonduplicative feature map.

- [ ] `M0.1` Inventory current pages, routes and calculators against the table above. In particular inspect `portfolio/history.py`, `portfolio/risk.py`, `portfolio/read.py`, `valuation/read.py`, `lookthrough/read.py`, `/holdings/[ticker]`, `/` and the in-progress watchlist. Mark each feature `reuse`, `extend`, or `new`; preserve existing user feedback and config versioning.
- [ ] `M0.2` Write `docs/analytics/methodology.md`: security return versus total return, FX translation, TWR, money-weighted return/XIRR, benchmark excess return, CAGR, volatility, downside deviation, drawdown, beta, Sharpe, concentration, ROIC, free cash flow, leverage, peer multiples and DCF. Define annualization, day count, accounting basis, negative earnings/FCF and missing-data rules.
- [ ] `M0.3` Define calculation contracts and method versions under a shared backend analytics package. Include decimal/currency-safe inputs and a single rounding policy. The frontend formats rather than recalculates canonical metrics.
- [ ] `M0.4` Add evaluation fixtures: cash-flow timing, splits, dividends, GBP/GBX, ADR ratio, renamed/delisted stock, restated filing, stale fund, partially covered peer set and revised macro release. Write expected figures independently from the implementation.
- [ ] `M0.5` Define minimum coverage thresholds per feature: a full portfolio return requires all material holdings and flows in the window; a peer median requires a stated minimum count and comparable fiscal periods; a fund overlap reports both covered and unknown fund weights.

**Exit checks:** current calculations have one named owner and method; a test can reproduce every figure from stored, as-of inputs; the feature map identifies no duplicate truth store.

## Phase 1 — Investable universe, discovery and screeners

**Dependencies:** Plan 1 identity (`D1`) and fundamentals/price/fund coverage (`D3`, `D4`, `D6`). **Outcome:** a Morningstar-style discovery workflow over the app's actual covered universe.

- [ ] `M1.1` Add an `investment_universe` read model from canonical securities/listings. Include live/delisted status, exchange, country, currency, asset type, issuer, available history and source access state. Keep historical membership for point-in-time screens; do not remove delisted names from past universes.
- [ ] `M1.2` Build backend screening predicates for country, exchange, sector/industry, size, price/liquidity, dividend yield, 1/3/5Y return, volatility, growth, margins, ROIC, leverage, valuation and coverage. Filter on a metric only when its published definition and as-of period match the user's choice.
- [ ] `M1.3` Add separate stock and fund screens. Fund filters: asset class, domicile, distribution policy, ongoing charge/expense ratio, fund size, benchmark, tracking difference where calculable, holdings date, concentration, sector/region and look-through coverage. Do not label unavailable fund NAV or fees as zero.
- [ ] `M1.4` Build market/sector peer baselines from **covered securities**, with count and geographic scope in each result. Let the owner choose home-market peers, global peers or a saved peer set. Exclude stale or incomparable financial periods with reasons.
- [ ] `M1.5` Add saved screens, named column sets, sorting, watchlist add, personal CSV export with source/provenance metadata, and a “why did this pass/fail?” detail for each screen row. Reuse the existing watchlist CRUD and owner-only access.
- [ ] `M1.6` Build `/discover` or an equivalent research-cluster page: filter builder, result count, coverage by filter, data date, unknown bucket and a one-click comparison tray. Add a minimal screen for no-key users so absence of optional providers is understandable.
- [ ] `M1.7` For historical screening/backtesting, freeze the universe, metrics and known-at cutoff at each rebalance date. Include delisted securities and transaction-cost assumptions; prohibit the current universe from being used retroactively.
- [ ] `M1.8` Stock-picking first filter (strategy principles 7a and 7b). Extend the existing screen (`steward/screen.py`, today six filed-statement metrics) with metrics read from the `M2.15` ratio library, so a ratio has one definition on the screen and on the ratios page:
  - value: FCF yield, EV/EBIT, earnings yield, gross profit / total assets;
  - momentum: 12-month total return excluding the latest month, from stored daily closes;
  - red flags: share-count change over 1 and 3 years (dilution), total-asset growth, accruals ratio ((net income − operating cash flow) / average assets), years since IPO, cash runway for unprofitable companies (cash / trailing operating cash burn);
  - insider-cluster flag: two or more distinct insiders with open-market purchases classed as opportunistic (the existing insider and PDMR classifiers) within 90 days.
  Add a composite "quality-value-momentum" score: each component as a percentile rank within the chosen universe and sector, combined with published, versioned weights (default quality 40 %, value 40 %, momentum 20 %), abstaining when a component's coverage is inadequate. Add a saved "idea pipeline" screen: top third on the composite, no red flag, and the speculative-tier requirements (2-year cash runway, dilution at most 10 % a year). Each row links to the `M2.14` financials and `M2.15` ratios pages and says why it passed or failed.

**Exit checks:** saved screens reproduce the same results for the same as-of date and method version; a free-plan-denied estimate filter is disabled with an explanation; coverage is visible before a filter is applied; no survivorship-biased backtest is described as a historical screen.

## Phase 2 — Company and stock analysis workbench

**Dependencies:** Plan 1 fundamentals (`D4`), graph/patents (`D5`), macro/trade (`D2`) and Phase 1 peer sets. **Outcome:** one evidence-linked analysis page per company and listing.

- [ ] `M2.1` Create an issuer-level overview and listing selector. Display business description, segments, products, countries, legal group, competitors, listing-specific price/currency, upcoming events and coverage score. Preserve the current holding page for position/thesis facts; link it to the issuer workbench.
- [ ] `M2.2` Show 5–10 year annual and 8–12 quarter financial tables/charts where available: revenue, operating income, EPS, operating cash flow, capital expenditure, FCF, assets, debt, cash and share count. Mark reported versus normalized, original versus restated, fiscal-period shifts and missing quarters.
- [ ] `M2.3` Compute growth/quality/financial-health metrics: organic-like growth only if disclosed; gross/operating/net margins, ROIC with stated invested-capital formula, cash conversion, accrual warning, balance-sheet leverage, interest coverage and dilution. Show components and exclusions on drill-down.
- [ ] `M2.4` Build peer compare with the same metrics and synchronized periods. Offer relative-to-first-period charts, currency conversion or native-currency view, peer median/range, fiscal-date distance, and a comparability warning for banks/insurers versus nonfinancial issuers.
- [ ] `M2.5` Add original valuation models: trailing multiples, normalized earnings/FCF, peer multiples, DCF with explicit forecast years, terminal assumptions and discount-rate components, reverse DCF/implied growth, and scenario ranges. Show a margin-of-safety calculation only against a user-selected scenario, with sensitivity grids and source links. No single opaque “true value.”
- [ ] `M2.6` Add an investment case: management guidance, company moat **evidence** (switching costs, scale, IP, network effects, regulation, or none), bull/bear drivers, customer/product concentration, capital allocation and key risks. Keep these as cited assessments separate from reported numbers. Avoid proprietary Morningstar rating names and scales.
- [ ] `M2.7` Add estimates where a tested free-access route and a full metadata record exist. Present analyst count, range, vintage and period; distinguish Finnhub event-calendar estimates from forward consensus and company guidance. Suppress estimate surprise percentages if metric basis or fiscal period differs from the actual.
- [ ] `M2.8` Include patent and business-graph tabs from `D5`: technology mix, patent-family trends, evidence-linked acquisitions/partnerships/competitors, and changes since the previous thesis review. Show confidence and ownership-mapping caveats.
- [ ] `M2.9` Generate a versioned “company report” using code-computed tables and model-written prose with sentence-level citations, conflict flags and an editable conclusion. Reuse the existing research/report verification pipeline.
- [ ] `M2.10` Add a capital-allocation history: acquisitions/divestitures, reinvestment, dividends, buybacks, share dilution and debt changes. Reconcile announced actions to cash-flow statements and share count; separate a company's stated capital-allocation policy from the observed result. Show per-share growth beside aggregate growth.
- [ ] `M2.11` Add leadership, governance and ownership evidence: dated executive/board roles from filings and confirmed graph edges, existing insider transactions, 13F holders where reported, and compensation or related-party issues only when the filing supplies them. An institutional manager's 13F position is not beneficial ownership or an endorsement.
- [ ] `M2.12` Add segment/product/geography analysis with reported segment periods, eliminations and changing definitions. Let the owner map segments to country, commodity, patent and trade contexts, but label the mapping estimate and keep unallocated revenue visible. Do not sum segment revenue without removing intersegment eliminations.
- [ ] `M2.13` If a compact stock assessment is useful, design an original, decomposable StewardAI rubric for financial durability, capital allocation, valuation assumptions and evidence uncertainty. Publish its inputs, weights, missing-data penalty, version and historical calibration; show component scores and abstain when coverage is inadequate. Do not recreate Morningstar's star or moat labels.
- [ ] `M2.14` Complete financial statements page per company (`/companies/[id]/financials`, linked from the holding page, screens and watchlist): the full income statement, balance sheet and cash-flow statement with every reported line item, not the selected lines of `M2.2`. Annual for up to 10 years and quarterly for up to 12 quarters, plus trailing twelve months. Two views: **as reported** (the issuer's own line items and labels from the XBRL/ESEF filing, in filing order, with dimensional detail available on expand) and **standardized** (mapped to a common chart of accounts so companies compare; every mapped line shows its source concepts, and unmapped amounts stay visible as "other"). Options: native currency or base currency at period-end/average rates (stated), units (thousands/millions), common-size (% of revenue or of total assets), year-over-year change, and per-share values on the diluted share count. Mark restated periods (original and restated both viewable), fiscal-year changes, missing periods and unreconciled facts (`steward.fundamentals.statements`). Every cell links to its filing, concept and period. CSV export with provenance.
- [ ] `M2.15` Complete ratios page per company (`/companies/[id]/ratios`) backed by one versioned ratio library (formula, inputs, period rule and exclusions per ratio, shared with `M1.8` screens and `M2.4` peer compare). Groups:
  - profitability: gross, operating, EBITDA, pretax, net and FCF margins;
  - returns: ROE, ROA, ROIC (stated invested-capital formula), ROCE, gross profit / assets;
  - growth: revenue, operating income, EPS, FCF and book value per share, 1/3/5-year CAGR;
  - cash flow quality: FCF conversion, operating cash flow / net income, accruals ratio, capex / revenue, capex / depreciation;
  - liquidity: current, quick and cash ratios, cash runway;
  - solvency: debt / equity, net debt / EBITDA, interest coverage, debt / assets, Altman Z-score (non-financials only);
  - efficiency: asset, inventory and receivables turnover, days sales outstanding, days inventory, days payables, cash conversion cycle;
  - per share and capital allocation: share-count change (dilution), dividends and buybacks per share, payout and total shareholder yield;
  - valuation (with the price date): P/E, forward P/E where a tested estimate exists (`M2.7`), EV/EBIT, EV/EBITDA, EV/sales, P/B, P/FCF, FCF yield, earnings yield, PEG, dividend yield, and the reverse-DCF implied growth rate from `M2.5`.
  Each ratio shows its history (annual and TTM), the company's own 5- and 10-year median, the peer median and the company's percentile within its `M1.4` peer set, with the formula and inputs on drill-down. A ratio whose denominator is zero, negative or meaningless for the industry (e.g. gross margin for banks) is "n/m" with the reason, never a number.

**Exit checks:** every financial value links to a period/source; bank metrics use an industry-appropriate method or are explicitly unavailable; valuation changes predictably with an edited assumption; a company report cannot invent a missing forward consensus; on the financials page the standardized statement totals equal the as-reported totals (unmapped amounts shown as "other"), and the balance sheet balances for every period shown; every ratio on the ratios page reproduces by hand from the cells it links to, and the same ratio has the same value on the ratios page, in screens and in peer compare.

## Phase 3 — Fund and ETF analysis workbench

**Dependencies:** Plan 1 look-through/ownership (`D6`) and returns (`D3`). **Outcome:** research on funds as funds, beyond their ticker charts.

- [ ] `M3.1` Build a fund identity/profile page for each covered share class: issuer, fund ISIN, domicile, benchmark, replication, distribution policy, currency, inception, assets, charge/expense ratio, factsheet date and source. Distinguish fund, share class, and exchange listing.
- [ ] `M3.2` Present holdings with as-of date, asset class, sector, country, currency, issuer-normalized security and residual cash/derivative/unknown weight. Show top holdings and a downloadable **derived** exposure table for the owner. Reuse existing `etf_snapshot` and `etf_holding` records.
- [ ] `M3.3` Compute fund concentration and style proxies from available holdings and fundamentals: top-10 weight, effective number of issuers, market-cap mix, value/growth metric distribution and weighted profitability. Name the methods as StewardAI proxies; never imply they are Morningstar Style Box categories.
- [ ] `M3.4` Compare fund price/NAV total return against its documented benchmark where suitable data exist. Show tracking difference, volatility and drawdown with the benchmark type, currency, dividend treatment and period alignment. If NAV or total-return benchmark is missing, limit the view to price performance.
- [ ] `M3.5` Compare two to five funds on costs, holdings overlap, geography/sector, concentration, return and look-through completeness. Report overlap both as `min(weight_A, weight_B)` summed by issuer and as a list of shared holdings; identify stale holdings dates.
- [ ] `M3.6` For bond funds with published holdings, compute weighted maturity/duration/credit-quality exposures only if the underlying issuer data actually provide them. Otherwise show the fund's stated factsheet characteristics, or unavailable; do not infer credit quality from issuer name.
- [ ] `M3.7` Add an original fund due-diligence checklist: costs, tracking evidence, replication method, securities lending where disclosed, domicile, distribution/tax facts, diversification, liquidity and holdings freshness. Show each criterion and its source rather than a proprietary-style medal or star; abstain on a summary score when critical fund inputs are absent.

**Exit checks:** fund weights reconcile with residuals; share classes of the same fund do not double-count in a comparison; tracking results never compare price return to total-return index without an explicit warning.

## Phase 4 — Portfolio accounting and performance

**Dependencies:** Plan 1 return/corporate-action engine (`D3`) and fund data (`D6`). **Outcome:** a reliable answer to “how did my investments do?”

- [ ] `M4.1` Reconcile Trading212 fills, dividends, deposits/withdrawals, fees, taxes, FX conversions and position snapshots into a dated portfolio ledger. Separate external cash flow from investment income; flag unsupported transaction subtypes and breaks against broker totals.
- [ ] `M4.2` Compute daily base-currency portfolio value from positions, cash and dated FX. Produce price, income, FX and fee components; account for trading holidays and stale prices. Add a reconciliation report between reconstructed value and Trading212 snapshot with tolerance and explanation.
- [ ] `M4.3` Compute time-weighted return from cash-flow-adjusted subperiods and money-weighted return/XIRR from actual external cash flows. Provide 1M/YTD/1Y/3Y/5Y/since-inception windows, annualize only appropriate periods, and distinguish personal return from market return.
- [ ] `M4.4` Implement realized/unrealized gain and tax-lot accounting (FIFO and average cost only where relevant/configured), dividend income, fees and withholding. Keep a jurisdiction-neutral accounting ledger and export; do not present a tax liability or filing advice.
- [ ] `M4.5` Add benchmark comparison with matched currency, dividend treatment and investable-period coverage. Allow blended benchmark weights with rebalance rules and a user-entered benchmark when free index history is unavailable. Show active return and its missing-data share.
- [ ] `M4.6` Compute holding/sector/country/currency return contribution and attribution over the same windows, reconciling to portfolio return within a defined residual. Continue the existing `contribution_to_return` path and waterfall UI; distinguish market movement from trades/cash flow and FX.
- [ ] `M4.7` Build performance views: value and contribution charts, cumulative and rolling returns, dividend/cash-flow timeline, fee drag, gain/loss and benchmark comparison. Every chart exposes price/FX freshness and a ledger-break badge.
- [ ] `M4.8` Holding-period tracking for strategy principle 10b (the Czech 3-year time test), built on the `M4.4` lots: per purchase lot, the buy date, the date it passes 3 years, and the months remaining; the year's running total of gross sale proceeds against the configurable CZK 100,000 annual exemption. Flag a lot that reaches its sell target within 6 months of the 3-year date (an alert that a full sale would forfeit the exemption), and show both dates in the thesis review pack. These are holding-period facts and owner-configured thresholds only, not a tax liability or filing advice, in keeping with `M4.4`.
- [ ] `M4.9` Satellites-versus-core comparison for strategy principle 11a: split the portfolio into the core ETFs (the tickers in the `max-single-position` rule's `exclude` list) and the individual stocks, and compute each sleeve's time-weighted return over a rolling 2 years after fees and FX costs, using the `M4.3` and `M4.5` methods. Show the annual gap in the monthly review's compliance table; when the stocks trail by more than the configured gap (default 2 percentage points a year), raise a review item proposing the 10-percentage-point shift to the core. Show the sample length and say "too early to judge" under 12 months of history.

**Exit checks:** independently calculated fixtures cover mid-period deposits, dividends, split, cross-currency buys, partial sales and a missing price; TWR is not distorted by a deposit; attribution plus residual reconciles to total return; the UI does not show a precise return when the ledger is incomplete; a partial sale consumes lots in the configured order and moves each remaining lot's 3-year date correctly; the two sleeves' returns, weighted, reconcile to the portfolio return.

## Phase 5 — Portfolio X-Ray, overlap and concentration

**Dependencies:** Phases 3–4 and Plan 1 macro/graph (`D2`, `D5`). **Outcome:** Morningstar-style look-through views built from dated fund and direct holdings.

- [ ] `M5.1` Extend the existing exposure panel into selectable X-Ray views: asset class, sector, industry, country, region, trading currency, issuer, fund, investment style proxy, market-cap band and fees. Each bucket shows its contributors and whether classification is reported, inferred or owner-assigned.
- [ ] `M5.2` Build Stock Intersection over direct shares plus ETFs/funds using canonical issuer, preserving listing/share-class paths. Show combined weight, direct weight, each fund's contribution, and concentration change if a fund is removed. Keep unsupported fund weight in an “unknown” bucket.
- [ ] `M5.3` Compute concentration metrics at security, issuer, sector, country and fund levels: top-10, HHI/effective holdings, single-issuer look-through weight and benchmark-relative over/underweight. Existing concentration metrics should be reused and extended, not duplicated.
- [ ] `M5.4` Add weighted portfolio fundamentals where valid: P/E using an earnings-yield aggregation method, P/B, dividend yield, profitability, leverage and fee drag. State non-meaningful aggregates (negative earnings, banks mixed with industrials, unknown fund portions) rather than forcing a number.
- [ ] `M5.5` Add country-of-revenue and supply/trade exposure as separate X-Ray dimensions **only** for companies with reported or owner-mapped data. Keep domicile, listing country, country of risk and revenue geography distinct.
- [ ] `M5.6` Build benchmark-relative X-Ray from an official/dated benchmark or labelled ETF proxy. Compare only like-for-like classifications and report proxy tracking and missing constituents. Do not claim historical index membership from today's ETF holdings.
- [ ] `M5.7` Allow a proposed-portfolio X-Ray: edit hypothetical weights or add watchlist securities without changing broker data, and show before/after exposure, overlap, fee and risk with coverage differences.

**Exit checks:** each X-Ray bucket sums to total portfolio value including cash/unknown; Stock Intersection retains the fund-to-underlying trail; “country” labels cannot silently mix issuer domicile and country of risk; the hypothetical view never alters saved holdings.

## Phase 6 — Risk, scenarios and rebalancing decisions

**Dependencies:** Phases 4–5 and Plan 1 macro data (`D2`). **Outcome:** decisions anchored to portfolio objectives and measurable uncertainty.

- [ ] `M6.1` Extend existing volatility, beta, drawdown, Sharpe and correlation with downside deviation, Sortino, value-at-risk/expected shortfall as clearly historical estimates, rolling correlations, diversification benefit and benchmark tracking error. Require minimum observations and show window/sample size.
- [ ] `M6.2` Create scenario shocks for equities, rates, FX, credit spreads, sector and country demand. Use historical sensitivities when statistically valid, owner-set assumptions otherwise; make the source of each sensitivity visible. BIS/ECB/OECD/World Bank/Comtrade series inform scenario narratives but do not imply a causal company beta by themselves.
- [ ] `M6.3` Calculate exposure to a theme or event through issuer, fund, sector, country, trade and currency paths. Avoid double-counting direct ownership and fund look-through; show unknown exposure and confidence.
- [ ] `M6.4` Extend drift rules to a rebalance workbench: target weights/bands, cash available, proposed buys/sells, expected post-trade X-Ray, estimated costs, and optional tax-lot consequences where ledger data support them. Keep it read-only until the owner takes action in Trading212.
- [ ] `M6.5` Add “what if” cases: new stock/fund, contribution/withdrawal, dividend reinvestment, FX move, benchmark change, or a thesis-triggered exit. Save named scenarios with assumptions and show how they differ from current holdings.
- [ ] `M6.6` Tie alerts to owner-defined rules: concentration, drawdown, earnings/thesis breach, unusual valuation assumption, macro credit signal, or patent/deal change. Existing alert deduplication and quiet-hour rules remain the dispatch path.
- [ ] `M6.7` Pre-trade check for strategy principles 8, 9 and 10, run on any proposed buy, add or sale (from the rebalance workbench `M6.4`, a what-if `M6.5` or chat). For a buy or add: the upside from the price to the thesis sell target against the return hurdle (default 30%), and the starting size that principle 8b allows for that upside and tier. For a sale: the lots it would consume, whether any is within 6 months of the 3-year time test (`M4.8`), and the year's exemption headroom. Also the post-trade cap, tier and core-share checks. For a switch (a sale funding a buy), add the switching rule of principle 9f from quant plan `Q8.5`: the expected-return gap against the hurdle (lot tax, costs, safety margin), the risk-adjusted ranking of both stocks (`Q8.4`, principle 7c), the risk-equivalent size of the replacement and the asymmetry gate (principle 8a). The result is a pass/flag list citing each principle; it never blocks or places a trade.

**Exit checks:** scenario totals reconcile to a complete shocked portfolio; a sensitivity with inadequate history is unavailable or explicitly user-assumed; recommendations cite a breached rule, KPI or entered scenario and do not imply an automated trade.

### Phase 6 (this branch)

Built in `steward/decision/` (risk report, shocks, themes, rebalance, lot impact, pre-trade, what-if, service) behind `/portfolio/risk/report`, `/portfolio/scenarios/*`, `/portfolio/rebalance`, `/portfolio/pretrade` and `/portfolio/decision/saved`, with the `/risk` pages (Risk, Stress test, Themes, What if, Rebalance, Before a trade). Migration `20261018100000` widens the `saved_analysis` kinds; saved items keep the request and the result as they stood and are archived, never edited. Nothing is sent to Trading212.

| Item | Status | Gap |
| --- | --- | --- |
| `M6.1` | Built, with limits | Downside deviation, Sortino, historical VaR and ES (95 and 99), tracking error, rolling benchmark correlation, a correlation matrix of the ten largest holdings and a diversification benefit. Each figure shows its sample size and is withheld below its minimum (`analytics/thresholds.py`). |
| `M6.2` | Built, with limits | Equity, rates, credit, FX, sector and country shocks. Equity betas are measured where the regression is valid, else owner-set, else the stated fallback; rates and credit need a recorded fund duration or an owner value. A holding with no valid sensitivity stays put and is listed with the reason. Parts reconcile to the shocked total. Macro series are shown as context only. |
| `M6.3` | Built, with limits | Selectors on sector, industry, country, revenue country, domicile, listing, currency, commodity, trade product and issuer. A holding counts once at its largest match; unknown weight, an upper bound and a confidence level are shown. |
| `M6.4` | Built, with limits | To target or to the band edge, with cash floor, FX and trade costs, FIFO lot consequences with the time test, drift before and after and the X-Ray after. Costs are assumptions entered on the page. |
| `M6.5` | Built, with limits | New security, contribution, withdrawal, dividend reinvestment, FX move (on the shock engine), benchmark change (on the risk report) and exit. Named cases are saved. |
| `M6.6` | Partial | New risk limits feed the existing breach and alert path: one-day VaR, tracking error, stress loss, US high-yield spread, valuation terminal growth and yearly growth. The patent and deal-change trigger is not built. |
| `M6.7` | Partial | Return hurdle, start size by tier, post-trade caps, sale lots and time test, and switch margin run as pass/flag. The asymmetry gate needs a bear case, and the risk budget and portfolio-improvement checks need a covariance model; none exists, so they are listed as "cannot check". Thresholds are now read from a `pretrade` block in the strategy config (defaults unchanged); a request may still override them. The strategy editor has no fields for the block yet, so it is set by editing the strategy JSON. |

**Phase 6 exit checks:** shock parts reconcile to the shocked total (tested); a holding without a valid sensitivity is listed, never silently moved (tested); a theme counts a holding once and keeps unknown weight (tested); a rebalance never sends an order; saved items reopen as they were saved.

#### Phase 6 open gaps

**Verification not done**
- No `/risk` page has been rendered in a browser or run against the live account. Web checks so far are typecheck, lint, colour lint and unit tests of the form helpers.
- Migration `20261018100000` (widens the `saved_analysis` kinds) has not been applied to a real database.
- The new routes (`api/decision_routes.py`) and services (`decision/service.py`, `series.py`) have no database-backed tests, because they need `STEWARD_TEST_DATABASE_URL`. The calculators (shocks, themes, rebalance, lot impact, pre-trade, what-if, risk statistics) are covered by hand-worked tests in `tests/test_decision.py`.
- The full backend and web suites were not re-run after the last edits; only the touched files' tests were.
- No component tests for the `/risk` views (rendering, save and open of saved items).

**Method limits**
- `M6.1`: statistics are descriptive of the sample, not forecasts; VaR and ES are historical. Holding-level figures cover only holdings with enough shared days, and the matrix shows the ten largest.
- `M6.2`: rates and credit shocks move nothing without a recorded fund duration (`fund_owner_fact`, `effective_duration_years`) or an owner-entered one. Equity beta needs at least 120 observations and R² of 0.05; otherwise the owner value or the stated fallback is used. Sector and country shocks are demand moves on top of the market, with no historical sensitivity behind them. Macro series (FRED) are context only and imply no company beta. BIS, ECB, OECD, World Bank and Comtrade series are not wired into the narratives.
- `M6.3`: confidence depends on the labels that exist; funds not looked through and missing labels count as unknown. Themes cover current holdings only.
- `M6.4`: costs are assumptions typed on the page. Lot consequences use FIFO and the time test from performance settings; units without a lot on record are listed. No tax-year exemption headroom is computed.
- `M6.5`: a new security uses only the history it has for risk figures. Saved cases store the inputs and the result as they stood and reopen as saved, not recomputed.
- `M6.6`: the patent and deal-change trigger is not built. The credit-spread alert reads the stored `us_hy_spread` series (FRED BAMLH0A0HYM2) only. Valuation flags apply only to saved valuation scenarios of held stocks, and only when a ceiling is set. The new limits are off until set in the strategy editor.
- `M6.7`: thresholds (return hurdle 30%, start sizes 5/3/1.5%, switch margin 5 pp, short-term tax rate 15%) are stored in the strategy's `pretrade` block and used when a check names none; they are not editable on the page or in the strategy editor form. The asymmetry gate needs a bear case; the risk budget and portfolio-improvement checks need a covariance model; none exists, so they read "Cannot check". No chat entry point yet.
- Rebalance and pre-trade are read-only; nothing is sent to Trading212.

**Product gaps**
- The what-if page does not show a side-by-side diff for saved cases against today's holdings beyond the before/after tables.
- `lib/nav.ts` lists one "Risk" entry; there is no deep link from a breached risk limit to the page that explains it.
- `ProposalSummary` shows seven X-Ray measures; the full per-lens before/after stays on `/xray/proposed`.

## Phase 7 — Research workflow and differentiated features

**Dependencies:** all prior phases. **Outcome:** capabilities beyond a conventional data dashboard.

- [ ] `M7.1` Add a thesis/decision journal per security and portfolio: initial hypothesis, valuation assumptions, expected catalysts, disconfirming evidence, target review date and an owner decision. Snapshot the evidence and methodology at decision time so later outcomes can be audited.
- [ ] `M7.8` Thesis fields for the strategy's thesis requirements (principle 5), added to `ThesisConfig` and the theses editor next to the price target: the reverse-DCF implied growth rate (filled from `M2.5` when available) and the base-rate check (how often companies of that size and sector have achieved that growth, with the source); a pre-mortem (the three likeliest reasons the stock loses 40 %, each also offered as an exit condition); the variant perception (what the owner believes that the market does not, and the evidence that would prove it wrong); conviction (1–5) and the main reason for owning it (quality, value, catalyst, momentum or other). Deep research pre-fills drafts with citations; the owner confirms them.
- [ ] `M7.9` Yearly decision scorecard (strategy principle 11b) built on the `M7.1` journal and the review decisions: share of sell targets hit and the median time to hit them; returns by conviction level and by main reason; buys compared with rejected candidates (ideas that reached deep research but were not bought, tracked from the decision date); outcomes of principle 9e profit-taking and principle 9d sales; every switch from the quant plan's switch log (`Q8.9`), comparing the stock bought with the stock sold from the switch date, with a recommendation to raise the switching safety margin when switches do not beat holding; and whether the risk estimates held (realized volatility and losses against each thesis's band and bear case). Show sample sizes and say "too few decisions" below a minimum; feed the results into the monthly review once a year.
- [ ] `M7.2` Add a “what changed?” feed that compares the last two verified states: fundamentals, estimate/guidance, ownership, price/valuation, competitor/deal graph, patent portfolio, macro/trade exposures and fund constituents. Sort by estimated portfolio relevance and cite the exact before/after observations.
- [ ] `M7.3` Build a sector/industry map from the covered company universe: peers, growth/profitability distribution, valuation spread, top products, patent areas, trade dependencies and credit cycle signals. Show how many companies and which markets underpin each statistic.
- [ ] `M7.4` Add event-to-portfolio impact paths: e.g. a supplier disruption -> confirmed supplier edge -> exposed issuer -> direct and ETF-held weight -> thesis KPI. Present uncertainty at each edge; only confirmed high-confidence paths may drive a high-severity alert.
- [ ] `M7.5` Add a data-quality cockpit: identity conflicts, stale prices, missing actions, unbalanced fund weights, inconsistent filing concepts, estimate access/coverage gaps, provider quota, macro revisions and research claims awaiting verification. Let the owner prioritize fixes by affected portfolio weight.
- [ ] `M7.6` Extend chat/research tools to answer comparative questions using deterministic analytics: “why did I underperform?”, “which fund duplicates this stock?”, “what happens if EUR falls?”, “which holdings rely on Chinese exports?”. Return formulas, coverage and citations; say unavailable when inputs are absent.
- [ ] `M7.7` Offer personal exportable owner reports with a manifest of sources, dates, coverage and calculation versions. Keep reports private by default; no licensing gate is required under the owner's stated permission.

**Exit checks:** a change item points to both old and new records; event-path confidence never exceeds its weakest edge; paid-only or missing vendor data cannot appear as a fabricated value in PDF/CSV/chat/export.

### Phase 7 (this branch)

Built so far: the decision journal and scorecard (`steward/journal/`, routes `/journal`, `/journal/scorecard`, `/journal/export/*`, pages `/journal`), the thesis fields in `ThesisConfig` and the theses editor, the data-quality cockpit (`steward/quality/`, route `/quality/cockpit`, page `/quality`) five chat tools (`chat/analytics_tools.py`) and event-to-portfolio paths (`steward/events/`). Migration `20261019100000` adds `decision_journal`, which is append-only (insert and select only; a review is a new row with `parent_id`). **`M7.2` and `M7.3` were added afterwards (`steward/changes/`, `steward/sectors/`, pages under `/insights`), both partial.**

| Item | Status | Gap |
| --- | --- | --- |
| `M7.1` | Built, with limits | Entries for a security or the portfolio: decision, hypothesis, valuation assumptions, catalysts, disconfirming evidence, review date, conviction, main reason, price. The thesis, holding weight, price, one-year volatility, active config version ids and every method version are stored with the entry. Reviews point at the entry they revisit. The snapshot does not include the valuation workbench scenarios, estimates, ownership or graph state. |
| `M7.8` | Built, with limits | Reverse-DCF implied growth, base-rate check with source, three-reason pre-mortem (one click adds them to the exit conditions), variant perception with its disproof, conviction 1-5 and main reason, all optional so old theses still load. The editor lists what a thesis still lacks. Deep research does not pre-fill drafts, and the implied growth can now be filled from the valuation (button "Fill from valuation", route `/companies/{ticker}/thesis-prefill`). **"Draft from research" (route `POST /companies/{ticker}/thesis-draft`)** now writes why-owned, exit conditions, three pre-mortem reasons and a variant perception from the stored research sentences, computed figures and report conflicts. Every line names the numbered facts it rests on; a line that names none is dropped and counted. It fills only empty fields, saves nothing, and leaves the base rate, conviction and main reason to you. One chat-model call, no web search. |
| `M7.9` | Partial | Sell targets reached and median days, returns by conviction and by main reason, bought against passed, price after trims and after sales, each with its sample size and "too few decisions" below five. Switches are unavailable (no switch log, `Q8.9`) and so is "did the risk estimates hold" (a thesis stores no volatility band or bear case). The monthly review now has a "Decision scorecard" section, and the scorecard page and route can be limited to one security or one main reason. |
| `M7.2` | Partial | `steward/changes/feed.py`, route `/insights/changes`, page `/insights`. Needs no new table: it reads the `known_at` stamp on stored records and compares what was known at the end of the earlier day (default: the previous portfolio snapshot) with what is known now. Areas: filed figures (revenue, gross and operating profit, net income, assets, equity), estimate vintages, price moves of 3% or more between the two snapshots, fund constituents between the last two holdings snapshots, new or changed company links, and macro revisions. Each item names the old and new records; sorted by portfolio weight; a first-time record is "new", not "revised". **Added later:** ownership (the two latest complete 13F quarters), insider net buying or selling (last 90 days against the 90 before), patents (empty until patent data is loaded), and a `change_alerts` job (daily 07:55) that raises an alert for a tier-1 non-rumour deal story or a new patent family. **Not compared: trade exposures.** The app's data is a few weeks old, so most filed-figure items are first loads; fund constituents need two snapshots of the same fund, and only two exist. No relevance score beyond portfolio weight.** |
| `M7.3` | Partial | `steward/sectors/map.py`, route `/insights/sectors`, page `/insights/sectors`. Sectors and industries of the companies with a stored profile (24 today): company and market counts, held weight, revenue growth and operating margin (latest filed year, from the peer-comparison engine), trailing price/earnings (provider figure), confirmed peer links inside the group. Every statistic shows how many companies stand behind it; a median needs three. **Added later: largest reported product lines, average country split and patent areas per sector, and a credit signal per sector (net debt to equity of the group's companies, the count above 1.0x, and the market-wide US high-yield spread beside it, labelled as not sector-specific). Not built: customs-based trade dependencies (needs the Comtrade key) and a true sector credit spread (none is stored). Coverage is the profiled companies only, so a sector with one or two names has a range and no median.** |
| `M7.4` | Partial | `steward/events/paths.py` follows an event at one company along supply-chain links (owner-entered and confirmed filing disclosures), downstream for a supplier disruption or upstream for a lost customer, up to three links. Each link is rated high, medium or low (stated share and a date within three years, no share, older), a path takes its weakest link, and only a confirmed event on high links reaching 2% of the portfolio can be rated high severity. Shown at the end of the path: weight (direct and through funds, combined), thesis indicators, and the revenue share for a single customer link. Route `/portfolio/xray/event-paths`, section on the supply-chain page. **Added later: an event feed (`/portfolio/xray/event-feed`, classified news mapped to paths); the `change_alerts` job (needs `OWNER_USER_ID`) raises a severity-3 alert for each `would_alert` item; each path splits its end weight into direct and through funds; the chat tool `event_paths` answers "what does an outage at X do to me". Only the supply-chain graph is walked, not patents, deals or macro exposures.** |
| `M7.5` | Partial | Stale or missing prices, unresolved identities, fund holdings that do not add to 100%, claims awaiting verification, open source conflicts, provider access problems, provider quota use and theses missing or incomplete, ranked by affected portfolio weight (each holding once). Missing corporate actions, inconsistent filing concepts, estimate coverage and macro revisions are listed on the page as not covered. An issue can be set aside for 7 to 90 days (migration `20261021100000`, append-only; it returns on its date and the check keeps running). |
| `M7.6` | Partial | Chat tools `stress_test` (the standard shocks), `theme_exposure` (any sector, country, currency, revenue country, commodity, trade product or issuer) and `decision_scorecard`, `performance_explain` ("why did I underperform": attribution bridge, largest detractors and contributors, benchmark comparison with its like-for-like flag) and `fund_overlap` ("which fund duplicates this stock": channels, weight reached twice; limited to the 100 largest issuers); each returns the formula, coverage and a citable source. The `event_paths` tool was added later; there is still none for the valuation workbench. |
| `M7.7` | Partial | `/journal/export/report` (JSON: journal, scorecard, cockpit, and a manifest with sources, dates, coverage per part and every method version) and `/journal/export/entries.csv`. Private: built when asked, stored nowhere. The manifest has `figure_sources`: one line for each journal entry, each scorecard figure (naming the journal entries behind it via `entry_ids`) and each cockpit check. `/journal/export/report.pdf` is a PDF made on the server (fpdf2, built-in fonts, so characters outside Latin-1 print as `?`); `/journal/export/report.html` is a print-ready page. The scorecard page has the PDF button. |

**Phase 7 exit checks (for what is built):** a journal entry cannot be changed or deleted (no route, no update grant); a review must name an entry about the same security; a scorecard section below the minimum reports no figure; a missing price or a currency mismatch gives no change, never a guess; a missing value in the export is null.

#### Phase 7 open gaps

**Verification not done**
- No `/journal`, `/journal/scorecard` or `/quality` page was rendered in a browser or run against the live account; neither was the new theses-editor section.
- Migration `20261019100000` has not been applied to a real database.
- The journal routes, `build_snapshot`, `scorecard` service, cockpit queries and the chat tools have no database-backed tests (they need `STEWARD_TEST_DATABASE_URL`). The pure parts (scorecard arithmetic, target-hit search, CSV, ranking, thesis fields, tool registration, web form helpers) are covered.
- The cockpit SQL (`identity_match_decision`, `etf_snapshot`/`etf_holding`, `web_fact`, `source_conflict`, `provider_access`, `provider_quota_usage`) was written from the migrations, not run.
- No component tests for the new pages.
- Full suites after the last edits: web typecheck, lint, colour lint and 894 tests pass; backend 1919 passed, 600 skipped (database-backed). `test_every_budget_construction_in_the_code_is_registered` fails on Windows unless `PYTHONUTF8=1` is set (it reads source files in the system code page); it passes with it. The Phase 6 migration lacked its rollback file, which `test_lifecycle_phase8` caught; both Phase 6 and Phase 7 rollbacks are now in `supabase/rollback/`.

**Method limits**
- Scorecard returns are price changes without dividends. Outcomes read the latest stored close, so a stale price series understates how long a decision has run; the currency of the stored series is assumed to match the currency recorded with the entry.
- A sell target is read from the thesis as it stood when the entry was written; an entry written for a stock with no thesis has none.
- "Passed" ideas are only those the owner recorded as `pass`; ideas that reached deep research but were never journalled are not tracked.
- The cockpit's stale-price limit (7 days) and quota warning (80%) are constants in `quality/cockpit.py`, not settings. Holdings without a stored price series are reported as having none, which includes cash-like instruments.
- Chat tools run the same standard shocks and theme engine as the pages; their answer is only as good as the labels and sensitivities those have.

**Product gaps**
- The journal has no link to the valuation workbench or to the holding page; the ticker is typed.
- A thesis review date and a journal review date are separate. Both now raise a severity-1 alert (daily `change_alerts` job) when due and not reviewed, within 30 days of the date.
- A snoozed data-quality issue cannot be marked "fixed" for good: it returns on its date if the check still finds it, and the snooze list is now part of the owner report.
- `M7.9` results are not yet part of the monthly review, and the scorecard cannot be limited to one security or one reason.
- The journal cannot attach a rejected idea from deep research automatically; a pass must be entered by hand.
- The theses editor shows what a thesis lacks but does not block saving without it, and the pre-trade check now flags a buy of a stock whose thesis lacks any requirement (core holdings are exempt). It is a flag, not a block.

**Not started (needs new data or models before any screen)**
- `M7.2` is built over `known_at` history for filed figures, estimates, prices, fund constituents, company links and macro revisions. It still needs before/after state for ownership, patents and trade exposure, and a relevance score beyond portfolio weight.
- `M7.3` is built for sector and industry statistics. It still needs top products, patent areas, trade dependencies and credit-cycle signals aggregated per sector, and profiles for more than the 24 companies that have one.
- `M7.4` still needs an event feed (today the owner names the company and how sure the news is), patent, deal and macro-exposure edges in the walk, the direct versus fund-held split of the end weight, and the wiring of a high-severity path into the alert path. The path calculator, edge ratings and weakest-link rule are built.
- `M7.6` has no tool for event paths or for the valuation workbench; `fund_overlap` finds only issuers inside the overlap report's top 100.
- `M7.7` is complete for what the app stores. Limits: the PDF uses plain text in the built-in fonts, with no charts.
- `M7.9` "did the risk estimates hold" reads the thesis's new bear-case price (kept in the journal snapshot): the share of buys that fell to it and the median worst fall. Entries written before a bear case existed have none and are not counted. There is no volatility-band comparison.
- `M7.2` items carry a 0-100 relevance score (60 points portfolio weight, 40 a fixed area and kind factor) and are sorted by it. The factors are stated, not learned or validated.
- A true per-sector credit spread cannot be built: no free source publishes one. The group's own leverage stands in for it.

**Data that needs the owner (cannot be fetched without credentials)**
- Patents: EPO OPS client id and secret (free signup). Until then patent views and the patent alert are empty, by design.
- Trade: a UN Comtrade key and configured slices.
- The deployed worker runs older code: redeploy this branch so `fund_issuer_facts` (expense ratio, fund size) and `change_alerts` run.
- Covariance risk check: `risk_contribution_cap` has no default (the strategy states no number); set it in the strategy config to make it a gate. The asymmetry gate reads the thesis's bear-case price (a field beside the targets in the theses editor), or one typed on the buy (`buy X bear 90`), which wins.

## Phase 8 — Validation, release and measurement

**Dependency:** Phases 0–7. **Outcome:** a research tool whose impressive breadth is supported by measured coverage and correct arithmetic.

- [ ] `M8.1` Maintain independent golden examples for stock, fund and portfolio calculations. Check accounting identities, return reconciliation, X-Ray bucket totals, valuation sensitivity, FX inversions, peer-universe membership and point-in-time screens.
- [ ] `M8.2` Perform a manual source-to-screen audit on at least 20 securities across regions, five ETFs, three corporate-action histories and multiple portfolio cash-flow windows. Compare every displayed fact with its cited filing, issuer report or official statistical source.
- [ ] `M8.3` Run a 30-day owner pilot. Measure feature usage, false alerts, coverage, stale/unknown weight, source failures, time to answer core research questions and owner corrections. Prioritize defects by portfolio value affected.
- [ ] `M8.4` Add accessibility and responsive QA for dense comparison tables, charts and evidence drill-downs; preserve keyboard access and explicit text labels for confidence/stale states already used in the app.
- [ ] `M8.5` Gate activation in the owner's personal app on tested free access, data quality and a zero-cost guarantee. Features whose core input has no accessible free source stay labelled partial/unavailable; do not quietly substitute a paid-only endpoint or invent proprietary Morningstar outputs.

**Exit checks:** every marketed feature has a defined coverage denominator, reproducible method and successful sampled source audit; no paid data or provider overage is required to use the baseline product.

### Phase 8 (this branch)

Built: the release gate (`steward/release/readiness.py`, route `/release/readiness`), the source-audit ledger (`steward/release/audit.py`, `/release/audit`), the pilot window and report (`steward/release/pilot.py`, `/release/pilot`, `/release/usage`), pages `/release`, `/release/audit` and `/release/pilot`, a golden-coverage map (`docs/analytics/golden_coverage.md`, checked by `tests/test_golden_map.py`) and a static accessibility ratchet (`apps/web/src/lib/a11y/`). Migration `20261020100000` adds `source_audit`, `pilot_run` and `usage_event` (insert and select only; a corrected audit result is a new row).

**Most of this phase is human work that code cannot do: the audit comparisons, the 30 days of use, and the decision to switch features on. What is built is the place to record them and the rule that holds a feature back until they are done.**

| Item | Status | Gap |
| --- | --- | --- |
| `M8.1` | Partial | The map ties each family the plan names (accounting identities, return reconciliation, X-Ray bucket totals, valuation sensitivity, FX inversion, peer-universe membership, point-in-time screens) to existing hand-worked tests and fails if one is renamed or removed. **No new calculations were added; the cases that already existed are only indexed.** The Phase 6 cases (risk statistics, shock reconciliation, theme exposure, rebalance, pre-trade) are now in the map too. The Phase 4 lot and attribution cases, the Phase 7 scorecard and the event paths are in their own test files and are not yet in the map. |
| `M8.2` | Partial: securities audited by script | The page counts clean subjects against 20 securities (3 regions), 5 funds, 3 corporate-action histories and 2 cash-flow windows. On 8 Oct 2026 Claude compared 44 stored annual figures (revenue and net income) for 23 holdings with the SEC XBRL companyfacts feed and loaded the result into the live ledger: **41 match across 21 securities in 2 regions (North America, Europe); 3 not checkable** (NU and TSM, whose latest 20-F is not in the feed yet; Asia-Pacific and Latin America stay uncovered). **Caveat:** the stored values were first loaded from this same feed, so this confirms period, unit and value were stored correctly, not that the filings are right. **Not done:** ETFs, corporate-action histories and cash-flow windows. (Correction: you do hold funds, the iShares Nasdaq 100 (SXRV), the iShares Diversified Commodity Swap (SXRS) and a Brent crude ETC (BRNT); they were missing from the look-through and valuation lists. On 8 Oct 2026 the Nasdaq 100 and commodity swap were added to the live Jobs config (version 58) and their snapshots and factsheets now load weekly. Their expense ratio and fund size are still empty because the deployed worker does not know the `fund_issuer_facts` job yet, so there is nothing to compare; the Brent ETC has no look-through provider.) The exit check is therefore still not met. |
| `M8.3` | Partial | A pilot window (30 days) counts pages opened, days used, alerts sent and the share rated wrong or irrelevant, provider and job failures, facts marked wrong, and today's flagged share of the portfolio with the largest issues. A defect log on the pilot page takes problems you find yourself, ranked by the share of the portfolio in the holding named (migration `20261021100000`, append-only; closing is a new row). **Time to answer a research question is not measured; coverage and stale weight are today's figures, not an average over the pilot; "priority by portfolio value" covers only issues that have a holding.** The page-use beacon sends the path only. |
| `M8.4` | Partial | The release pages and the Phase 6–7 tables now have captions and column scopes. A source scan (`lib/a11y`) fails when a file gains an unnamed table, an unscoped header or an unnamed svg; **61 existing findings in other pages are recorded as a baseline, not fixed.** Not checked at all: contrast, keyboard order, screen-reader output, responsive layout of dense tables and charts. That needs a browser pass with a real assistive tool. |
| `M8.5` | Built, with limits | Twelve features, each with core inputs (stored data and free-tier endpoints), shown as ready, partial, not tested or unavailable, with three checkpoints (free access, source audit, zero cost). A feature can be switched on only when all three pass; the zero-cost audit comes from `audit_zero_cost`. **Nothing is switched off by the gate: it reports, and the app does not hide a feature that fails.** The input list is hand-written, and "ready" means the table has at least one row, not that the data is fresh or complete. A feature's audit now reads "passed" only with at least 3 matched facts on at least 2 subjects (fewer reads "thin"), and an empty endpoint manifest no longer passes the zero-cost check. Source audits are not required per region or per input. |

**Phase 8 exit checks (for what is built):** the audit ledger has no edit or delete route; a "match" needs a source value and a link; one differing fact blocks both its feature and the sample; a feature with a paid-only or missing core input is "unavailable" and cannot be activated; a failing zero-cost audit blocks every feature; with nothing rated there is no false-alert rate, not zero.

**Phase 8 open gaps (beyond the table)**

*Release gate*
- A feature's audit needs 3 matched facts on 2 subjects (constants in `release/audit.py`); the gate does not require a minimum number of regions or inputs per feature, and the 20 / 5 / 3 / 2 sample is judged across the whole app, not per feature. A feature can still be activatable on three comparisons.
- A feature with no listed input (the journal) is "ready" automatically. "Ready" for stored data means at least one row exists in the table, not that it is recent, complete or for the owner's holdings.
- The feature list, its inputs and the store-to-table map (`FEATURES`, `STORES`) are hand-written; a new marketed feature is not gated until someone adds it. The web page keeps a second copy of the feature ids (`FEATURE_CHOICES`); a backend test now fails if the two lists differ.
- Only endpoints in `provider_access` are checked. Keyless regulator sources (EDGAR, N-PORT, ESEF, issuer files) have no access record, so features that depend only on them are judged by stored rows alone.
- The "unavailable / untested / partial" decision reads the detail text of an input; a new access state would need the rules updated.
- The zero-cost audit needs the endpoint manifest; if it is empty the gate now treats it as not passed.
- The gate shows the answer on `/release` only. Nav, feature pages and the chat do not yet show a "partial" or "unavailable" label from it.
- Counting rows runs one query per store each time the page loads.

*Audit ledger*
- Region, subject and field are still free text. Known region spellings (EU, Europe, EMEA, US, USA, APAC, and similar) are folded to one name, and case and spacing no longer make a second fact, but any other spelling ("Deutschland", "S. Korea" against "South Korea") counts separately. Corporate-action histories and cash-flow windows are not linked to the records they describe.
- Results are entered by hand for one owner; nothing prompts which securities to sample, and the sample is not drawn at random or stratified (for example by market cap or listing venue).
- A re-check replaces a result only when subject, feature and field are identical.
- The ledger cannot attach the source document; it stores a link, which may change or break.

*Pilot*
- The window starts today in UTC dates; a new pilot hides the earlier one's report (the rows stay). Only one report is shown at a time and it cannot be exported.
- Page use is counted by the browser beacon on every navigation, including refreshes, release pages and background route changes; a failed call is dropped silently, so counts are a lower bound.
- False alerts count only alerts you rated; unrated alerts say nothing about accuracy. Missed alerts (a real event with no alert) cannot be measured.
- "Coverage" is the cockpit's flagged share today, not a coverage percentage per feature, and no pilot figure is turned into a target or a pass mark.
- The defect log ranks by the weight of one named ticker; a defect that affects several holdings or a whole feature is ranked as "not tied to a holding".

*Tests and tooling*
- The new tests cover the pure functions only (audit summary and validation, gate rules, false-alert rate, form building, the accessibility scan, the golden map). The SQL, routes, readiness counts and pilot report have no test against a database.
- `tests/test_lifecycle_phase8.py` would exercise the new rollback file only when `STEWARD_TEST_DATABASE_URL` is set; it was not run.
- The accessibility scan reads source text, so a table built from a spread of props or a component that renders a table is not seen; it does not look at `td`-only tables or ARIA grids.
- `scripts/check-colors.test.ts` has a 5-second limit and timed out once when the backend suite ran at the same time; it passes alone.
- The golden map includes the Phase 6 cases but not the Phase 4 or 7 ones, and it only checks that a test exists, not that the test is still meaningful.

*Not done from the plan text*
- `M8.2` ETF, corporate-action and cash-flow comparisons, and a third region for securities (done for 21 securities on 8 Oct 2026, see the table), and the `M8.3` 30-day use; the exit check "successful sampled source audit for every marketed feature" is therefore not met.
- `M8.4` responsive and keyboard QA of dense comparison tables, charts and evidence drill-downs, and checking that confidence and stale states keep their text labels in every page.
- `M8.5` the decision to switch features on is the owner's; the app does not store that decision or enforce it.

**Follow-up pass (Phases 6–8):** closed the gaps that code alone could close: stored pre-trade thresholds, the two missing chat tools, cockpit snoozing, event paths, the defect log, the per-feature audit minimum, region and fact-name folding, the feature-list parity test and the Phase 6 golden-map rows. Migration `20261021100000` (snooze and defect tables) has, like the others, never run against a database, and the new routes and pages were not exercised or rendered. Still open and needing a person or new data: the rest of `M7.2` and `M7.3` (see their rows), the patent and deal-change alert (`M6.6`), the asymmetry and covariance checks (`M6.7`), the audit comparisons, the 30-day pilot and the browser accessibility pass.

**Migrations on the live project (StewardAI, 8 October 2026):** applied `portfolio_performance`, `missing_fk_indexes`, `saved_analysis`, `decision_journal`, `release_validation` and `quality_followup` through the Supabase tool, which records them under new version numbers. **Not applied: `20261014100000_performance_config` and `20261018100000_decision_saved_kinds`**, because both drop and re-add a check constraint (on `config_version` and `saved_analysis`) and the permission check refused them; apply them yourself or allow it. Until then the performance settings stay in `performance_setting` rather than the versioned config domain, and the new saved kinds (shock scenario, what-if, rebalance plan, theme) are refused by the database. `20261017100000_xray_owner_input` was already there. The six applied migrations were created and not exercised by the app; no row was written.

**Verification not done:** migration `20261020100000` has never run against a database; the new routes, queries and pages were not exercised against real data or rendered in a browser; there is no database-backed test for the readiness counts, the audit insert or the pilot report.

## Suggested delivery slices

The phases are sequential on shared foundations, but each should ship a useful slice before the next starts:

1. **Find and compare:** discovery, saved screens and a sourced stock workbench (`M0–M2`).
2. **Understand funds and returns:** fund pages, reconciled performance and X-Ray/overlap (`M3–M5`).
3. **Decide and monitor:** scenarios, rebalancing, decision journal, change feed and quality cockpit (`M6–M8`).

Each slice should ship with its access and coverage indicators. “Deep global coverage” should mean broad **measured** reach across markets and sources, alongside explicit gaps where the free data ecosystem cannot yet provide the same depth.

## Explicit limits and next analytics backlog

These are important to an “ultimate” analysis tool but cannot be represented as complete solely from Plan 1's free data. Implement the honest partial version first, then evaluate additional free primary sources through Plan 1's access tests.

| Capability | What to build now | What stays unavailable or narrow |
| --- | --- | --- |
| Analyst consensus/revision analytics | Event-estimate comparison and cited company guidance; accessible issuer/free consensus where available. | Broad global consensus, revisions and analyst-count trends without a verified feed. |
| Fixed income research | Issuer-reported bond-fund characteristics and BIS/ECB credit-cycle context. | Security-level yield curves, covenant analysis, ratings and full bond-fund look-through without instrument terms/holdings. |
| Options and derivative exposure | Show broker-reported cash/derivative residuals and user-entered scenario sensitivities. | Option Greeks, volatility surfaces and strategy P&L without an accessible free contracts/quotes feed. |
| Sustainability and controversy | Cite issuer disclosures and independently verified events in research. | Proprietary ESG ratings or a claim of consistent global emissions coverage. |
| Tax planning | Lot-level gains, withholding and fee ledger with exportable evidence. | Jurisdiction-specific tax liability, wash-sale rules and filing calculations until jurisdiction and official rule data are scoped. |
| Historical factor attribution | Transparent value/quality/size/momentum proxies on covered securities, with an observed universe and window. | Claims of institutional factor-model precision or unbiased global backtests without full point-in-time history. |
| Private companies and deal intelligence | Dated Diffbot/PDL candidates, public announcements and patent links in the company graph. | Complete private-company financials, transaction values and cap tables. |
| Broad portfolio optimization | Constraint-based hypothetical weights using covered returns, costs and owner risk limits. | “Optimal” trade recommendations when expected returns, covariance history or tax consequences are unreliable. |

For each backlog item, capture the missing input, markets affected, access status, current workaround, and a concrete activation test in the data-quality cockpit. Prioritize by affected portfolio value and owner decisions, not feature count.

## Product references

- [Morningstar Investor portfolio features](https://www.morningstar.com/help-center/portfolio/intro-to-portfolio)
- [Morningstar X-Ray dimensions and fund analysis](https://www.morningstar.com/help-center/portfolio/xray)
- [Morningstar screener workflow](https://www.morningstar.com/help-center/screener/create-screen)
- [Morningstar company-report topics](https://www.morningstar.com/help-center/stocks/company-reports)
- [Morningstar's proprietary stock-rating methodology](https://www.morningstar.com/help-center/morningstars-approach-to-investing/morningstars-stock-ratings)
