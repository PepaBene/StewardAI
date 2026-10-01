# Free data sources plan: deep audit (backend, Phases 0-8)

Audited at commit `63bbe93` on branch `claude/clever-albattani-30f3ke`. Eight read-only auditors reviewed one area each. Everything is offline: no adapter was checked against a live provider, so provider-payload findings are labelled as such.

Status keys: **CBE** confirmed by execution (a script or query reproduced it); **CBR** confirmed by reading (exact lines and caller/guard search); **SUSP** suspected (needs a recorded live payload or a runtime check).

I re-checked these myself before including them: ECB monthly parse, `price_bar` source CHECK, `fundamentals` source CHECK, missing jsonb codec, provider-state latch, LEI-confirms-security. All reproduced as described.

## A. Critical / high: features that do not work, or give wrong numbers

| ID | Status | Where | Problem |
|---|---|---|---|
| A1 | CBE | `migrations/20261001140000_intraday_prices.sql:27` | `price_bar.source` CHECK allows only `stooq`,`yahoo`. EODHD and Alpha Vantage price fallbacks (D3.2/D3.3) raise `CheckViolationError` after the API call is made and credits are spent; the step fails daily. Nothing reads those bars anyway (A2). |
| A2 | CBR | `reference/price_series.py` `pick/choose_series` | Only stooq/yahoo series are ever chosen, but `run_price_history` marks the holding as sourced from eodhd/AV and removes the gap, so the UI shows no gap while no reader consumes the data. |
| A3 | CBE | `migrations/20261001120000_valuation.sql:19` | `fundamentals.source` CHECK allows `sec`,`esef`,`finnhub`. The Alpha Vantage overview fallback raises on insert; `run_valuation` does not catch `PostgresError`, so one failure aborts the job for later tickers and repeats daily. |
| A4 | CBE | `macro/collect.py` `parse_ecb_series` | `date.fromisoformat` rejects `2026-07` / `2026-Q2`. 8 of 9 default ECB series are monthly/quarterly and parse to zero rows, silently. D2.4 is non-functional. |
| A5 | CBE | `macro/providers.py` SDMX parser + `public_collect.py` | Only flat `dataSets[0].observations` is parsed; a series-shaped SDMX-JSON body returns `[]` (silent NOT_COVERED). Wildcard series keys would merge all countries under one hardcoded geography. |
| A6 | CBE | `migrations/20260930182242` `record_provider_attempt` | Every attempt (429, 5xx, timeout, empty-2xx) overwrites `provider_access.state`; collectors gate on `state == 'available'`. One transient failure disables a provider until the monthly, off-by-default `source_audit` re-probes. `retry_at` is stored but not enforced by the governor. |
| A7 | CBE | `reference/matching.py:28-72` | LEI/CIK (issuer-level) can *confirm a security* match, so an ADR and its ordinary share confirm each other (score 0.95). Conflicting ISINs are not rejected. Breaks the Phase 1 exit check. |
| A8 | CBE | `sources/store.py` + `source_observation_identity` index | Index omits period_start, unit, currency, dimensions; `revision` is always 0. Restatements, segment variants and quarter-vs-YTD rows with the same period_end are silently dropped. Displaced preferred rows stay `published=true`. |
| A9 | CBE | `market_analysis.py` `financial_peer_metrics`, `free_sources_routes.py` peer-metrics | No jsonb codec is registered, so `dimensions` arrives as a `str`; the `isinstance(dims, dict)` guard never excludes segment facts. Alphabet revenue came out as a product line (7.0e9 vs 1.9e11) and growth as -87%. The unit test passes dicts, hiding it. Same string-for-jsonb issue likely affects other routes. |
| A10 | CBE | `market_analysis.py:125-250` | Peer metrics group by `(ticker, period_end, basis)`: 3-month and 6-month facts collapse; units are never compared (USD revenue / EUR gross profit); debt concepts overlap (last alphabetical wins); the latest period is the dei cover date so every metric is null; ROIC/FCF conversion with negative denominators give nonsense. |
| A11 | CBE | `market_analysis.py` | IFRS alias set covers only revenue, gross profit, net income. Operating income, pretax, tax, borrowings, cash and cash flow have no `ifrs-full` aliases, so IFRS peers give None and cross-basis peers are dropped with no reasons (D4.6 requires reasons). |
| A12 | CBE | `fundamentals/collect.py:154` | The statement fact store holds essentially revenue + shares + dei. No income-statement lines, balance sheet, cash flow, debt, leases, taxes, R&D, buybacks, pensions, no US text blocks, no segment profit/assets/capex, no SEC Financial Statement datasets (D4.1/D4.8). `fiscal_period`, `currency`, `issuer_id`, `evidence_id` never set; `dei:EntityCentralIndexKey` stored as a number, losing leading zeros. |
| A13 | CBR | `financial_statement_fact` readers | Nothing reads or sets `published` / `validation.reviewed`; peer and history use every row. No reconciliation of extracted totals to filed statements (D4.8). |
| A14 | CBR | `valuation/read.py:~665` | Market cap = ADS price x shares x `per_listing`; should divide by the ADS ratio. P/S overstated by ratio² (TSMC about 25x). P/E and P/B are correct. |
| A15 | CBE | `market_analysis.py:105-115`, `free_sources.py:497-511` | Total return: a dividend followed by a split is reinvested unadjusted (2.02% vs correct 1.01%); duplicate split rows from two sources are applied twice (+100% vs 0%); confirmed spin-off/merger/delisting/symbol-change rows are ignored, so a spin-off still returns "available". `price_raw` is split-adjusted, not raw. |
| A16 | CBE | routes `/returns`, `/prices/diagnostics`, `free_sources_routes.py:741` | Stooq bars have `currency NULL`, so returns say "missing or mixed currency"; Yahoo bars live under a different symbol than the routes select; cross-source disagreement joins on symbol+currency, so it never fires. No scheduled job computes returns; `GET /returns` writes. |
| A17 | CBR | repo-wide grep | Nothing ever inserts into `security_listing`. `POST /holdings/{t}/corporate-actions` always returns 422, EODHD/AV collectors always skip, `price_bar.listing_id`/`mic` stay NULL. Total return also needs `bar_type='raw'`, which no storable source writes. |
| A18 | CBE | `trade_observation.reporter` vs `exposure_mapping.geography` | Comtrade stores M49 numeric codes; exposures use ISO. Trade-exposure alerts (`free_source_alerts.py:384`), holding `trade_exposure` rows, and `/research-context/country/*` trade claims never match when geography is set. The only test uses NULL geography. |
| A19 | CBE | `free_source_alerts.py` patent_status + `_raise` | When the per-run cap is hit nothing is recorded but state still advances to the new status, so the dropped lapse alert is never re-evaluated. |
| A20 | CBE | `company_research.py:384-453` | EPO OPS parser returns `[]` on the documented OPS exchange-document structure (reads attributes where OPS uses child elements). The test fixture is invented. Fixture shape is the auditor's recollection of OPS (SUSP against a recorded payload). Family key is the bare doc-number (no country, no OPS family-id), so offices are not merged and EP/US numbers collide. |
| A21 | CBE | `store_patent_family`, `patent_trend_rows` | `dated_subsidiary_path` only needs a non-null `valid_from`; never checked against a confirmed subsidiary edge or the priority date; `valid_to` ignored. The "subsidiary patent not attributed without a dated path" exit check is met only superficially. |
| A22 | CBE | `store_company_edge`, corporate-action and `company_event` upserts | Status CASE keeps `confirmed` sticky: an owner rejection/dispute of a confirmed edge, event or corporate action is silently ignored while the API returns an id. Vendor re-sync overwrites `evidence_tier` (primary -> vendor) while status stays confirmed, which drops the edge out of the alert guard. A disputed edge is downgraded to `suggested` on re-sync. |
| A23 | CBR | `news/regulatory.py:405-420`, D5.8/D5.9 | SEC full-text shortcut is not replaced: hits are still tagged supplier/customer by keyword adjacency; nothing reads `filing_hit`; no passage fetch, direction resolver, review queue, or TED adapter. D5.9 deal state machine, 8-K item extraction, dilution/leverage recompute are absent; deal states have no transition rules and re-upserts reset `known_at`. |

## B. Medium: wrong or fragile behavior

**Governance / identity**
- B1 CBE: `source_audit` cannot recover a hard-blocked endpoint after a key change (it updates the hash but does not clear `hard_blocked`; `fund_probes` does). The audit reports QUOTA_EXHAUSTED for a hard block (`sources/audit.py:355-377`).
- B2 CBE: ESMA `parse_firds` drops CANC records before picking the latest per MIC, so a cancelled listing stays `ACTIVE`; the repo's own test (`test_reference_data.py:109`) asserts the buggy output. `listing_as_of` filters on current status, so a delisted line vanishes from historical screens and it has no `known_at` filter (unused in src).
- B3 CBR: `issuer_alias`/`issuer_relationship` have no writer; no delisting/rename regression fixture exists (D1.9); `superseded_by_id` never written; no as-of reader for document versions (D1.8).
- B4 CBE: `retry_after_at` raises `OverflowError` on `inf`/huge `Retry-After`; callers don't catch it.
- B5 CBR: identity override route accepts `confirmed` with no ids (null-security link) and returns 500 on FK errors.
- B6 CBE: `listing_lifecycle_snapshot` and other UNIQUE constraints with nullable columns accumulate duplicates (NULLs distinct).
- B7 CBR: docs/seeds overclaim: several providers seeded `available` without a probe (so the audit gate is vacuous); provider-access.md "hard-block until probe" is not what the seed does.
- B8 SUSP: `access_from_http` maps 402 to PAID_ONLY, which hard-blocks permanently; some providers use 402/403 for quota.
- B9 CBR: usage ledger has no job id; `reset_timezone`/`reserved` columns unused; Gleif/ESMA clients treat 429 as a plain error.

**Macro**
- B10 CBR/CBE: Every run re-inserts observations (`vintage=known_at.isoformat()` is unique per run) so `on conflict do nothing` never fires and tables grow unbounded; the provider vintage is discarded; `revision` never increments (also true of `trade_observation`).
- B11 CBR: ALFRED call uses `output_type=4` and sends no `units`: revisions would not be fetched (SUSP, FRED semantics from memory), and `us_cpi` gets index levels labelled `%` beside the `pc1` series.
- B12 CBR: World Bank coverage/metadata block is nested inside the SDMX loop and overwritten with NOT_COVERED per SDMX series; never written when no SDMX series is enabled.
- B13 CBR: ALFRED storage runs outside the try; an error fails the job after observations are committed, and `found` events are lost for good.
- B14 CBE: Comtrade parse: Re-export/Re-import mapped to export/import (unique key drops one); `netWgt or qty` falls through on 0; quantity unit attached to weight; relies on `flowDesc` text; HS revision labels (`H6`) never match user-entered `HS2022`; no concordance.
- B15 CBR: D2.1 columns (`unit_multiplier`, `seasonal_adjustment`, `transformation`, `release_calendar`, `geography_definition`) exist but no code writes them; OECD UNIT_MULT/ADJUSTMENT ignored.
- B16 CBE: exposure trigger caps geography+HS rows against one 100% total, so both cuts cannot be entered.
- B17 CBE: unmapped tickers get every country's indicators as "context"; 5y/10y ranges include superseded vintages; macro read filters mappings by `valid_from` not `created_at` (look-ahead).
- B18 CBR: ECB backfill gets `release_date = first-run day`; WB `lastupdated` is read per-row but is in the page header in the real payload (SUSP).
- B19 CBR: `parse_period`/`_date` raise on `2026-S1`, `2026-W05`; QuotaExceeded not caught in WB/Comtrade steps; Comtrade `body.get` on a list raises.
- B20 CBR: D2.8/D2.10 largely templated text; `macro/exposure.py` is dead in production.

**Prices**
- B21 CBE: return-window coverage is vacuous (`expected = max(sessions, bars)`), so 10 bars in a 43-weekday window score 1.0; the portfolio "complete" flag inherits it.
- B22 CBR: `exchange_session` table is not used by `exchange_open()`, monitor, intraday freshness, or release jobs; half-days unsupported; no tests for holiday-but-open or ad hoc closure.
- B23 CBR: GBX/GBP: EODHD/AV bars store listing currency as quote unit with no unit normalization; `GBp` dividend currency can't be expressed (`^[A-Z]{3}$`); no ADR ratio, withholding or FX-date handling in returns.
- B24 CBR/CBE: Alpha Vantage account budget has no reserve (a Stooq outage can spend all 25 and starve Company Overview); two-step reservations burn the account credit when the second raises; HTTP-200 `Information` denials are recorded as TEMPORARILY_FAILED, not PAID_ONLY.
- B25 CBE: `company_research_observation` has no unique key, so exposure/PDL refreshes duplicate rows; commodity "cents per pound" stored as USD; PDL called on every click with no cooldown.
- B26 CBR: corporate actions cannot be retracted (confirmed/rejected sticky).

**Fundamentals**
- B27 CBE: `valuation/fundamentals._by_concept` takes the first concept with any fact (an FY2018 `RevenueFromContract...` beats FY2024 `Revenues`); `debt_value` returns only `LongTermDebtCurrent`.
- B28 CBE: `latest_periodic` returns a `10-K/A` (Part III only) as the annual report, so the original 10-K is never parsed.
- B29 CBR: point-in-time: peer, Finnhub peer and IR-release upserts reset `known_at=now()`; peer status can't un-confirm; SEC `known_at` uses filing date midnight rather than acceptance time; ESEF `known_at` falls back to `now()`.
- B30 CBR: ESEF `fiscal_period` is `str(fact.end.year)` for every fact; segment history can union old and recast members.
- B31 CBR: a transient Alpha Vantage error upserts an empty body over good overview data and caches it for `full_refresh_days`.
- B32 CBR: forward_observation duplicates on every refresh; consensus groups across providers/currency; IR registry is a manual POST (no fetch/parse) so D4.10 actual-vs-consensus is absent.

**Funds / ownership**
- B33 CBE: 10b5-1 negations ("not adopted a Rule 10b5-1 plan") read as plan=True; the form-level checkbox marks every row planned (an existing test encodes this). Both drop trades from net discretionary.
- B34 CBR: dual-class tickers sharing a CIK lose insider data for all but the first ticker (`insider_filing` keyed on accession with one `ticker`).
- B35 CBR: one bad filing aborts the whole insider/control-stake run (and blocks it every run until it ages out).
- B36 CBE: `fund_probes` classifies "Limit reached... upgrade your plan" as `paid_only` and hard-blocks (premium words checked before quota words).
- B37 CBE: DTD/entity guard bypassed by UTF-16 documents in nport, schedule13, insider, xbrl parsers (expat limits mitigate; sources are EDGAR).
- B38 CBR: base look-through relabels unaccounted fund weight as known "Fund cash & derivatives" (the aggregate path does it right).
- B39 CBR: fund expense-ratio PIT filters on holdings `as_of` not `fetched_at`; the upsert rewrites history.
- B40 CBE: `nport.public_after` assumes calendar quarters (overstates by up to ~2 months for fiscal-quarter funds); N-PORT amendments missed for up to 100 days.

**Phase 7 / 8**
- B41 CBE: credit-stress dedupe key omits source and geography (second geography alerts nothing, and leaves no trail row).
- B42 CBR: the classifier output schema still allows the three system-only event types (only the prompt was filtered); a model can label news `credit_stress`.
- B43 CBR: graph-event "confirmed" has no evidence guard though docs say so; `company_event` has no `decided_at`, so a late confirmation is outside the 3-day window; "confirmed" evidence tier is caller-asserted.
- B44 CBR: several `as_of` leaks: `patent_trend_rows` called without `when`; edge/event statuses are current; coverage ledger is current; null `as_of` legacy rows match any cutoff.
- B45 CBE: `redact()` misses `api_token=`, `subscription-key=`, short quoted JSON keys, env-style names (`FRED_API_KEY=`), URL-encoded forms, userinfo; and over-redacts "token expired".
- B46 CBR: **Phase 8 gates are advisory**: `require_domain_gate`, `publish_gate`, `disable_policy`, `budget_from_access`, `verify_hard_stop` are called by tests and read-only routes only; no alert, valuation, or publishing path consults them.
- B47 CBE: reconciliation gate passes with 30 copies of one sample (no uniqueness, future `sampled_at` counts as fresh, caller supplies `stored_value`, no reviewer); signal gate has the same future-date and no-reviewer holes and ignores scoped failing audits.
- B48 CBR: 6 of 9 D8.6 scenarios test local oracle helpers, not the real extractors; shadow sign-off can be reached with `window_days<30`; price reconciliation ignores source/bar_type.
- B49 CBE: test guard does not clear proxy env vars (a loopback proxy lets real HTTPS out; observed in this sandbox, so lower impact on a plain CI runner); `.env` file still feeds `Settings` during tests; `NetworkAccessBlocked` subclasses `AssertionError` so `except Exception` can swallow it.
- B50 CBR/CBE: no rollback file for `20261004110000`; `MIGRATION_ROLLBACK_END` is hard-coded; the `price_bar` rollback claims no data loss but fails when raw+adjusted rows exist; rebuilt-CHECK rollbacks fail if newer values exist.
- B51 CBR: `audit_zero_cost` inspects only DB seed rows (35 `unverified_not_blocked` warnings still `passed=True`); `verify_hard_stop` uses the in-memory governor only; `zero_cost_gate` job fails on a safely hard-blocked endpoint that later records `temporarily_failed`.
- B52 CBR: ~30 FKs lack covering indexes (e.g. `source_observation.{issuer,security,listing}_id`, `source_evidence.document_version_id`, `identity_match_decision` x5).

## C. Low
`claim_sentence` prints 0.004 as `0`; `company_context` dedupe key drops period_start/unit; consensus `revision_since_previous` is cross-provider; `deals().current_state` ordering; patent alert dedupe is permanent per transition (flap lost); `claim_id macro:{id}` unstable across re-fetches; alerts only look back 3 days (job outage loses alerts); crash between `raise_events` and `_record` leaves an untrailed item; NaN/Infinity accepted by macro parsers; trade metrics blend flows and count World as a partner; Form 4 sell-to-cover phrase misses and "Mar. 1" adoption dates unparsed; PDMR natures regex without word boundaries (parser only); `unknown_by_fund` overwrites on duplicate ISIN; fund coverage ledger marks historical constituents after two snapshots; fund factsheet FK cascade deletes history on prune and a pin/prune race raises; `fund_characteristic_audit` grows daily; `apps/web/README.md` has a stale sentence about the three event types; 5 pre-existing web test failures unrelated to this work.

## D. Plan gaps, stated plainly
Not implemented or only manual: D4.8 SEC notes/datasets and reconciliation, D4.10 IR fetching and parsing, D5.8 passage review, D5.9 deal extraction and state machine, D5.10 successor/parent reconciliation, D5.11 statement-level Wikidata provenance, D6.1 factsheet fields (names unverified), D3.6 withholding and FX-date reconciliation, D3.7 benchmark completeness, D3.10 consuming `exchange_session`, D1.8/D1.9 as-of readers and lifecycle regression fixtures, D2.1 columns populated, UK PDMR retrieval, the 30-day shadow run itself.

## E. Candidates investigated and rejected (false positives)
- Route authorization: all 204 `APIRouter` routes depend on `require_owner`; only `/health*` are open. OpenAPI and `schema.d.ts` are current.
- RLS/grants on new tables: present (owner read, `app_backend` full, anon none); `provider_quota_status` is `security_invoker`.
- `PostgresRequestGovernor` atomicity: 40 concurrent reserves granted exactly 25; unknown providers are denied.
- `source_record_evidence` CHECK rebuild chain keeps earlier values (20261003110000/120000 rebuild from the live constraint). The 20261001131038 rebuild dropped `reference` and it was restored by 20261002130000; on a DB already holding a `reference` source row, 20261001131038 itself would fail (CBR, fresh DBs pass).
- Key leakage via logs/URLs: scrubber covers `SecretStr` fields; Comtrade key goes in a header; error texts carry no URLs (only the `redact()` gaps in B45).
- Alerts causing thesis breaches: breaches come only from the earnings check; the alert module cannot create them.
- Credit-stress crossing/revision logic and trade-exposure percent math are correct apart from A18/B41.
- 13D/G group stake is max not sum; Form 4 code table maps correctly; aggregate bucket math and unknown-weight handling are correct; PIT snapshot selection is correct.
- `macro_series_source_check` narrowing is intentional; retention does not delete data needed for replay or open conflicts.

## F. What was not examined
Frontend; `cnb.py`; GLEIF/Wikidata/OpenFIGI parsing beyond the paths above; lookthrough `read.py` helper lines ~330-560; `etf_issuers.py` Vanguard parsing; production-data migration behavior; every provider endpoint/quota against live documentation.
