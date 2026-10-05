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

## Phase 8 — Validation, release and measurement

**Dependency:** Phases 0–7. **Outcome:** a research tool whose impressive breadth is supported by measured coverage and correct arithmetic.

- [ ] `M8.1` Maintain independent golden examples for stock, fund and portfolio calculations. Check accounting identities, return reconciliation, X-Ray bucket totals, valuation sensitivity, FX inversions, peer-universe membership and point-in-time screens.
- [ ] `M8.2` Perform a manual source-to-screen audit on at least 20 securities across regions, five ETFs, three corporate-action histories and multiple portfolio cash-flow windows. Compare every displayed fact with its cited filing, issuer report or official statistical source.
- [ ] `M8.3` Run a 30-day owner pilot. Measure feature usage, false alerts, coverage, stale/unknown weight, source failures, time to answer core research questions and owner corrections. Prioritize defects by portfolio value affected.
- [ ] `M8.4` Add accessibility and responsive QA for dense comparison tables, charts and evidence drill-downs; preserve keyboard access and explicit text labels for confidence/stale states already used in the app.
- [ ] `M8.5` Gate activation in the owner's personal app on tested free access, data quality and a zero-cost guarantee. Features whose core input has no accessible free source stay labelled partial/unavailable; do not quietly substitute a paid-only endpoint or invent proprietary Morningstar outputs.

**Exit checks:** every marketed feature has a defined coverage denominator, reproducible method and successful sampled source audit; no paid data or provider overage is required to use the baseline product.

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
