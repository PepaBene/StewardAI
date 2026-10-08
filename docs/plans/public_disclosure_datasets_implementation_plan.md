# Public-disclosure datasets implementation plan

Research snapshot: 29 September 2026. Companions: [free data sources plan](free_data_sources_implementation_plan.md) and [Morningstar-style features plan](morningstar_features_implementation_plan.md).

## Goal, scope and hard boundaries

Build the additional datasets in the owner's two frameworks from **free, accessible sources**, then turn them into company, stock and portfolio decisions. The owner has confirmed permission for the intended personal use, so licensing is not a blocker in this plan. This plan covers institutional ownership; insider transactions; customer, supplier and revenue-dependency relationships; historical analyst consensus; individual analyst estimates where actually accessible; estimate revisions; point-in-time global fundamentals; public deal/transaction terms; and segment-level estimates. It also covers the shared filings, event, evidence and entity-resolution systems needed to make those datasets credible.

The product is the owner's personal, single-user, self-hosted app. Its data is for private research and portfolio decisions; sharing, resale and public distribution are outside this plan. Use the owner's permission as the authorization assumption. Test actual authentication, free access, retrieval stability and rate limits before scheduling each collector. Never pay, enter a trial that converts to paid, bypass technical access controls or manufacture a consensus from management guidance.

**Definition of done:** a dataset must have an evidence-backed schema, dated coverage and uncertainty, an owner-visible use in a screen or alert, an as-of query, offline fixtures, an access record, and measurable accuracy. Ingesting a document alone is not completion.

## Frontend visualization contract

Use the established web app bespoke SVG chart system in `apps/web/src/components/charts/` and `lib/charts/`, alongside existing evidence, provenance, freshness and coverage patterns. Reuse a treemap, Sankey, heatmap, candlestick, line/bar or timeline primitive only when it matches the data; build specialized views for ownership rank-flow, disclosure lifecycles or dependency networks where needed. Pair every chart with an accessible semantic table/list, exact source/evidence drill-through and visible units, denominator, fiscal period, known-at date and coverage. Missing data is not zero; suggested/inferred, reported and confirmed values must have distinct encoding. Keep control, institutional ownership, fund look-through and insider transactions in separate visual channels.

## What StewardAI already has and what this plan extends

| Existing path | Current useful base | Extension here |
| --- | --- | --- |
| `sources/edgar.py`, `news/feeds.py`, `earnings/locate.py` | SEC submissions, filing discovery and selected earnings exhibits | Versioned filing/exhibit corpus, structured deals, agreements, guidance, ownership and relationship evidence. |
| `fundamentals/collect.py`, `sources/xbrl.py`, `valuation/collect.py` | Shares, selected valuation facts and simple segment revenue | Point-in-time statements, dimensions, notes, restatements and reconciled segment history. |
| `ownership/collect.py`, `sources/form13f.py`, `ownership/read.py` | US 13F for selected held CUSIPs and managers | Wider tracked universe, amended-position history and separate 13D/G and N-PORT channels. |
| `ownership/insider_collect.py`, `ownership/insider.py` | US Forms 3/4/5 XML and raw codes | Footnotes, derivative terms, plan flags, amendments and economic classification. |
| `news/regulatory.py`, `news/pipeline.py` | SEC keyword-derived *suggested* customer/supplier edges | Cited passage, direction, named/anonymous counterparties, materiality, confirmation and rejection. |
| `sources/esef.py`, `sources/gleif.py`, `sources/esma.py` | Selected IFRS facts, LEI relationships and current venue identity | Dimensional IFRS, historical corporate structure and listing lifecycle. |
| `lookthrough/`, `portfolio/`, `monitor/` | Fund holdings, positions, portfolio analytics and monitoring | Historical ownership/dependency/transaction exposure and evidence-linked alerts. |

Use the existing domain tables wherever they already carry the right truth. The new shared tables represent documents, observations and claims; they should reference the existing 13F, insider, financial and relationship rows rather than creating a rival copy.

## Feasibility and expected coverage

| Dataset | Free primary route | Practical result | Product use and honest limitation |
| --- | --- | --- | --- |
| US institutional positions | SEC 13F, plus N-PORT for funds | Strong delayed US coverage; manager and fund views differ. | Holder/flow trend and fund overlap; neither is real-time nor complete beneficial ownership. |
| Major/control stakes | SEC Schedule 13D/G; national major-holder disclosures | Strong US, selective non-US. | Control-change and activist-event alerts; never sum 13D/G with 13F. |
| Insider transactions | SEC Forms 3/4/5; national PDMR/director releases | Strong US, selective non-US. | Discretionary purchase trend, role and dilution context; transaction codes alone are insufficient. |
| Named/anonymous customer-supplier dependency | Filed reports, cited disclosures, issuer IR, public procurement awards | Partial, evidence-rich. | Concentration, dependency graph and portfolio shock propagation; undisclosed links stay unknown. |
| Historical published analyst consensus | Versioned issuer-hosted consensus files and any accessible free estimate feeds | Sparse and issuer-selected without a broad feed. | Estimate history and actual/consensus comparison; do not present issuer-selected files as a market-wide feed. |
| Individual broker estimates | Accessible free public disclosures or owner-provided source access | Usually sparse or unavailable for technical/data-coverage reasons. | Show verified named observations; otherwise a coverage gap, never reconstruct a hidden panel. |
| Estimate revisions | Consecutive compatible public consensus snapshots | Partial and event-driven. | Direction, magnitude and breadth of revisions; no interpolation to daily data. |
| Point-in-time global fundamentals | SEC filed XBRL/notes, ESEF and vetted national registries | US strong, Europe partial, rest of world adapter-dependent. | As-known-at valuation, quality and historical screens; missing periods remain missing. |
| Public deal and financing data | SEC current/registration/proxy filings, exhibits, issuer and exchange notices | Detailed for material reported deals, globally uneven. | Deal timeline, dilution, leverage and scenario impact; undocumented private terms remain unknown. |
| Segment-level analyst estimates | Issuer-published segment consensus and accessible estimate files | Sparse. | Segment growth bridge versus actuals; management targets and modelled owner scenarios are separate. |

### Candidate source registry and technical gates

| Source | Discovery and access rule | Initial fields | Gate |
| --- | --- | --- | --- |
| [SEC EDGAR APIs and bulk data](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Use submissions/companyfacts, filing archive and official bulk datasets with declared user agent and [fair-access controls](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data). | Filed statements, 13F, 3/4/5, 13D/G, 8-K/6-K, registration/proxy documents. | Store accession, accepted/filed time and amendment links; cache documents, avoid full-corpus crawling. |
| [SEC Financial Statement and Notes datasets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets) | Monthly bulk backfill and targeted filing checks. | Detailed tagged facts, dimensions and selected notes. | Dataset observations must reconcile to their filed version; do not confuse monthly publication with filing availability. |
| [SEC N-PORT datasets](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets) and insider datasets | Public regulatory datasets, target only matched issuers/funds. | Fund holdings and derivative exposures; insider trades/roles. | Separate reporting lag, availability and ownership meaning by form. |
| [ESMA ESEF](https://www.esma.europa.eu/issuer-disclosure/electronic-reporting) / filing host | Retrieve original filing packages and versioned metadata. | IFRS primary statement tags, dimensions, issuer extensions and note blocks. | Notes may be block-tagged; a numeric note requires passage/table extraction and validation. |
| [FCA National Storage Mechanism](https://www.fca.org.uk/markets/primary-markets/regulatory-disclosures/national-storage-mechanism) and UK issuers | Public viewing/download and issuer announcements, after retrieval-method test. | TR-1, PDMR, annual reports and transaction notices. | The described API is for Primary Information Provider submissions; find a usable public read route. |
| [UK Companies House](https://www.gov.uk/guidance/companies-house-data-products) | Free API/bulk products with registration and rate checks. | Legal entity, filings, officers, persons with significant control. | PSC is company-control information, not listed-security institutional holdings; treat personal data carefully. |
| [Japan EDINET](https://disclosure2.edinet-fsa.go.jp/week0020.aspx) | Register for API key; verify current public API and rate limits. | Annual/securities reports, large-holding/insider and transaction disclosures where structured. | Pilot after language, taxonomy, identity and retrieval checks pass. |
| Other national regulators and exchanges | One adapter per jurisdiction (for example BaFin, AMF, AFM, CONSOB, ASX). | Major holdings, insider/PDMR and issuer events where published. | Add after a tested free route and reliable ID match. |
| Issuer IR and consensus pages | Maintain a per-issuer registry of stable URLs and version history. | Guidance, consensus files, segment consensus, deal releases, customer disclosures. | Test file availability, date/version detection and metric definitions. |
| [EU TED Search API](https://docs.ted.europa.eu/api/latest/search.html) | Query targeted buyers/suppliers and award notices. | Procurement awards, parties, CPV, value, period and lots. | An award is evidence of a contract, not proof of total customer revenue or ongoing supply. |
| [GLEIF Level 2](https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-2-data-who-owns-whom) | Reuse existing LEI collector and dated records. | Direct/ultimate *accounting* parent, successor and legal-entity changes. | Never label the accounting parent as beneficial share ownership. |
| [SEDAR+ public interface](https://sedarplus.ca/) | Active Canadian filing discovery pilot under the owner's stated permission; test registration, search/export flow, stable document IDs, incremental polling and rate controls. | Canadian annual/interim reports, ownership, insider and transaction disclosures where available. | Treat authentication, anti-bot or missing bulk endpoints as technical integration problems; fall back to issuer-hosted filings for uncovered documents. |

## Architecture and data contracts

### Four coordinated systems

1. **Filings engine:** source registry → incremental discovery → accessible fetch → content hash/version → document sections/tables/XBRL → extracted claims → cited evidence → review and publish. Structured form fields take precedence over prose extraction. Store both the original text location and normalized result.
2. **Estimates engine:** issuer IR discovery → access check → versioned file → metric/period/basis extraction → compatible snapshot chain → revision and actual-comparison calculations. Management guidance, company-selected analyst consensus, named broker estimates and owner scenarios are different series.
3. **Events engine:** SEC/regulator/issuer event discovery → duplicate cluster → event type and participants → terms → state transition → valuation/portfolio impact. Announced, agreed, approved, closed and terminated are distinct states, each supported by a dated document.
4. **Entity and point-in-time layer:** connect CIK/LEI/ISIN/FIGI/MIC/security/listing, preserve aliases and corporate actions, resolve document people and companies to canonical entities, and answer every query using what was known at the chosen cutoff.

Use PostgreSQL as the initial graph store (`relationship_claim` and `entity_relationship` plus indexed edge queries). Introduce a separate graph engine only if measured query depth/latency justifies its cost. Keep deterministic calculations in the backend; use AI to propose passages and explanations, never to silently publish numerical claims.

### Minimum schema contract

| Record | Keys, dates and invariant |
| --- | --- |
| `disclosure_source` | Source/endpoint/jurisdiction, free-access record, retrieval method, throttle, last check and enabled state; the owner's permission is a project-wide assumption. |
| `disclosure_document` and `document_version` | Regulator ID/accession, issuer ID, form, fiscal period, source URL, original SHA-256, language, accepted/filed/published/retrieved/first-seen timestamps, supersedes/amends ID, version status. A refetch of identical bytes does not create a new version. |
| `evidence_span` | Document version plus page/section/table/row/character offsets, excerpt or allowed reference, extraction method and confidence. A material claim has at least one inspectable span. |
| `disclosure_observation` | Typed metric, entity/security/segment, period, value/unit/currency/basis, dimensions, evidence ID, source-precedence rule, valid interval and `known_at`. Null is distinct from zero and “not disclosed.” |
| `extracted_claim` | Typed claim, subject/object, direction, polarity (`asserted`, `denied`, `superseded`), evidence, review state, confidence, valid period and publication/knowledge time. Machine suggestions never appear as verified facts. |
| `public_estimate_snapshot`/`estimate_value` | Issuer, publisher, file version, publication time, provider/panel description, analyst count if disclosed, metric, scope/segment, fiscal period, currency, GAAP/IFRS/adjusted basis, mean/median/range if given. |
| `ownership_disclosure` | Holder/group and security class, form channel, reported shares/percent, voting/economic interest where stated, filing/measurement date, amendment, evidence. Positions, beneficial stakes and fund holdings remain distinct types. |
| `insider_transaction_enrichment` | Link existing transaction row; economic class, acquired/disposed, discretionary flag, derivative linkage, footnote/plan flags, role and review decision. Retain original reported code/values. |
| `relationship_claim` | Provider/customer IDs or anonymous placeholder, contract/product, direction, share/amount with denominator/period, start/end, source and verification status; store source-specific claims before a confirmed graph edge. |
| `deal_event`/`deal_state_transition` | Event ID, type, participants, announced/effective/closed times, state, consideration/financing terms, currency, stake, conditions, source spans and amendment lineage. One transaction may have many state/term versions. |
| `coverage_status` and `quality_review` | Entity/metric/jurisdiction/period, available/stale/paid-only/technically unavailable/disputed reason, review assignee, decision, reviewer time and audit history. |

Every owner-only read API returns `source`, `published_at`, `known_at`, `as_of`, `method_version`, `coverage` and a document link when applicable. An as-of query includes a row only if `known_at <= as_of`; for restatements, return the latest version **known at that as-of**, not today's corrected value. Preserve `event_effective_at` separately from `known_at` so a backdated disclosure cannot leak into a historical backtest. RLS stays owner-only and the backend service role has only required write privileges.

### Quality and coverage contract

- Reconcile numeric observations to units, currency, sign, scale, fiscal period, share class and balance/segment totals. Preserve original strings and parser version.
- Maintain an entity-match decision with identifiers, candidate score and rejection reason. A name-only candidate goes to review; a relationship with an unresolved counterparty cannot be a confirmed named edge.
- Record negative/contradictory evidence and supersession; do not delete a wrong earlier extraction. Each published insight must be reproducible from its input observation IDs and calculation version.
- Report coverage by market, fiscal year, security class, issuer size, form type and dataset. Never generalize a US 13F result to worldwide institutions or a public issuer consensus to all analyst opinions.
- Use source-specific state machines for `not_disclosed`, `not_covered`, `paid_only`, `authentication_failed`, `retrieval_failed`, `parse_failed`, `unresolved_identity`, `under_review` and `verified`. The UI must distinguish these.

## Phase P0 — Free access, sample corpus and baseline

**Dependency:** Free data plan D0 and identity baseline. **Exit:** every pilot source has a tested free route or a documented technical/paid-only gap; no production collector is enabled from a marketing claim.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- No production chart is needed during source feasibility. Capture the planned user-facing coverage states and provenance fields in sample fixtures so frontend work can begin against stable examples while access checks proceed.


**Backend**
- [ ] `P0.1` Create `docs/data/public-disclosure-source-registry.md` with each specific form, API endpoint or issuer page, jurisdiction, registration/authentication method, cost, rate limit, document formats, publication lag, tested date, responsible owner and technical stop condition. Link to the access table in D0.1/D0.7 and record the owner's permission as the shared assumption.
- [ ] `P0.2` Assemble a reproducible 45-issuer corpus: at least 15 US, 10 ESEF Europe, 5 UK, 5 Japan, 5 Canada and 5 other jurisdictions; include an ADR, dual listing, fund, delisted issuer, renamed issuer, conglomerate and a company with a material disclosed customer. Use documents from distinct years and amendments. Map to existing held/watchlist identifiers without storing personal transactions in fixtures.
- [ ] `P0.3` Capture 10 samples each for 13F amendment, 13D/G group, Form 4 purchase/sale and non-discretionary activity, N-PORT, 8-K deal/exhibit, XBRL note, ESEF block note, SEDAR+ Canadian filing, issuer consensus version and relationship disclosure. Record exact expected parsed fields, published dates and manual ground truth.
- [ ] `P0.4` Test SEC fair-access behavior, conditional fetch, archive URL stability and bulk files; test FCA public download separately from its submission API, EDINET key registration, Companies House/TED read routes, issuer IR pages and SEDAR+ search/export. For SEDAR+, design a Canadian pilot around stable IDs, incremental discovery, rate controls and fallback issuer files. Record technical failures as integration gaps to solve, not licensing blocks.
- [ ] `P0.5` Inventory current SEC/ESEF/ownership/insider/news tables and API consumers. Draw a migration map that identifies which existing row is extended, which new record is shared evidence, and which old suggestion must remain unverified. Baseline current dataset coverage and known false-positive rate.
- [ ] `P0.6` Define release thresholds before coding: required evidence precision, identity match precision, numerical reconciliation, freshness and accessible-source coverage for each dataset; assign manual adjudication samples and an explicit zero-dollar cost check.


## Phase P1 — Filing corpus, identity and point-in-time foundation

**Dependency:** P0. **Exit:** a filing, a later amendment and a restatement can be queried at three historical cutoffs without future knowledge appearing.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Create the disclosure evidence browser as a searchable filing timeline with filing date versus accepted/published/known-at markers, amendment/supersession links and a coverage ribbon by issuer and period. A filing opens the exact cited page/table/span. Show source status and historical-known-at cutoff clearly.


**Backend**
- [ ] `P1.1` Add additive Supabase migrations for the shared schema above, with unique document/version keys, indexes for `(issuer_id, known_at)` and `(subject_id, object_id, valid_from)`, owner RLS and migration tests. Retain old IDs and foreign keys; use references/backfills, not a breaking rewrite of held-ticker tables.
- [ ] `P1.2` Implement the source registry and fetch governor under `sources/`: free-access and authentication check before requests, request budget, user agent, retry-after, incremental cursors, checksum deduplication, failure classification and observability. Archive fetched original documents and link them to their source URL/hash for point-in-time replay.
- [ ] `P1.3` Build document discovery for SEC submissions and targeted archive exhibits, then ESEF/issuer IR pilot. Parse filing index, primary document, exhibit list and XBRL package. Store accepted, filed, published, first-seen and retrieved dates separately; missing dates stay null with an explanation.
- [ ] `P1.4` Add parser outputs for document sections, HTML tables, XML/XBRL facts, footnotes and text blocks with stable evidence offsets. Store extraction model/rule version; changes create new claims without silently replacing earlier claims. Quarantine malformed or inaccessible documents.
- [ ] `P1.5` Extend `reference/identity.py` with legal entity, security, share class, listing, CIK, LEI, ISIN/FIGI, MIC, issuer aliases and dated successor rules. Require a reviewed mapping before joining a disclosure to a portfolio instrument; support anonymous counterparties without inventing a company ID.
- [ ] `P1.6` Implement `as_of` repository methods and a provenance read endpoint. Query one selected issuer at pre-filing, post-filing and post-amendment cutoffs; verify values, relationships and events match contemporaneous knowledge. Show source version and evidence span in the review UI.
- [ ] `P1.7` Add a reviewer queue for low-confidence parsing, entity ambiguity, contradictions and source-format changes. Decisions record the original candidate, reviewer and reason; a denied or superseded claim is auditable and excluded from authoritative calculations.


### Implementation status and open gaps after P0–P1 (updated 2026-10-07)

Built and tested: migration `20261007100000_disclosure_foundation.sql` (+ rollbacks), source registry and fetch governor, SEC discovery, deterministic parser with evidence offsets, `as_of` repository and provenance endpoints, reviewer queue, identity rules, offline fixtures, and the evidence browser at `/disclosures` (typed sample mode). Evidence: `tests/test_disclosure_*.py`, `apps/web/src/lib/disclosure/model.test.ts`. The gaps below are **not** done; later phases must not assume them.

| Gap | Task | Why it is open | What closes it |
|---|---|---|---|
| 0 of 110 P0.3 samples captured | P0.3 | Manifest and expected fields exist, but no real documents were pulled and hand-checked | Capture 10 per category through the fetch governor, record manual ground truth |
| Release thresholds unmeasured | P0.6 | Thresholds are defined in `docs/data/public-disclosure-release-thresholds.md`; nothing has been sampled against them | Run adjudication samples per dataset before its first release |
| No coverage baseline | P0.5 | Row counts and current false-positive rate need the deployed database | Run the inventory queries against the deployed DB and record results in the migration map |
| Corpus has no LEI/ISIN/FIGI | P0.2, P1.5 | Identifiers are filled only after a reviewed match; `resolve_via` is set for every non-CIK issuer | Run GLEIF/OpenFIGI resolution, reviewer confirms |
| ESEF / issuer-IR discovery not implemented | P1.3 | Only SEC discovery exists | Build ESEF filing-index discovery (needed by P2.5) |
| No scheduled discovery job | P1.3 | Discovery runs on demand, not from the collector | Wire a job after the SEC access record is confirmed on the deployed host |
| Four source routes unresolved | P0.4 | SEC FSN dataset URL, FCA request shape, EDINET key, SEDAR+ portal session not proven | Record each as tested/failed in the registry; failures are integration gaps, not licensing blocks |
| DB tests need a database URL | P1.1 | Suite needs `STEWARD_TEST_DATABASE_URL`; not re-run after the final refactor in the session that built it | Run `pytest tests/test_disclosure_db.py` against a disposable Postgres in CI |
| UI not visually checked | P1 frontend | Logic is unit-tested; no browser pass was done | One screenshot pass at desktop and phone width; keyboard pass on the cutoff slider |
| Live endpoints not exercised from the UI | P1 frontend | UI was built against typed fixtures; live mode compiles but has no data to show yet | Verify once an issuer has stored disclosures |

## Phase P2 — Point-in-time financial statements and notes

**Dependency:** P1; free data plan D4.1/D4.8/D4.9. **Exit:** reconciled US statements and a smaller ESEF pilot support historical valuation without restatement leakage.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Use statement line-item small multiples for as-filed trends, with restatement/version overlays; a cash-flow waterfall to explain operating cash → capex → free cash flow; and a debt/lease maturity timeline stacked by year and currency. Segment revenue/profit should be a treemap or mosaic with reported totals and eliminations retained. Put note values beside the cited source passage and never silently combine incompatible fiscal periods or dimensions.


**Backend**
- [ ] `P2.1` Define versioned metric taxonomy and accounting basis: revenue, gross/operating/net income, cash flows, cash/debt, shares, capex, tax, working capital, lease obligations, debt maturities, repurchases, dilution and pension/commitments. Store fiscal start/end, instant versus duration, original concept and taxonomy version.
- [ ] `P2.2` Backfill SEC companyfacts and targeted filed instances for the corpus. Parse context IDs, units/scales, dimensions and issuer extensions; use the SEC Financial Statement and Notes datasets for targeted historical comparison. Link every chosen fact to the filing available on the as-of date.
- [ ] `P2.3` Extract segment revenue, profit, assets, capex and geography/product facts with axes/members intact. Flag segment redefinitions and recast history. Do not sum overlapping geography and product axes, and show unknown/eliminations explicitly.
- [ ] `P2.4` Extract selected text-block note facts through cited tables/passages. Validate debt maturity buckets to reported total, lease schedule to liability basis where possible, share repurchases to period and class, and customer percentages to denominator/period. Send non-numeric or ambiguous blocks to review.
- [ ] `P2.5` Pilot ESEF statements on the European corpus. Preserve taxonomy extensions, currency conversion source, detailed tagged primary statement and block-tagged note distinction. Compare with SEC 20-F where cross-listed; record reconciliation conflicts, not a silent provider override.
- [ ] `P2.6` Define jurisdiction-adapter interface for EDINET, SEDAR+ and later national registries: discovery, form taxonomy, currency/unit, publication date, XBRL or text extraction, identifier mapping, authentication and coverage report. Enable pilots after P0 access checks; no “global fundamentals” label until measured coverage supports it.
- [ ] `P2.7` Expose as-filed statement history, segment bridge and selected note drill-down in company analysis. Recompute margins, ROIC, debt maturity and cash conversion with input citations, coverage denominator and formula version; allow the owner to choose as-of date for a past investment decision.
- [ ] `P2.8` Test restatement, amended filing, changed segment taxonomy, negative cash flow, unit multiplier, fiscal-year change and missing note. Require statement/segment arithmetic within documented tolerances before a value feeds a valuation or screener.


## Phase P3 — Institutional and major ownership

**Dependency:** P1 identity and existing 13F collector; P0 national-source access tests. **Exit:** 13F, beneficial stakes and fund holdings are separate dated layers with useful change signals.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Show ownership as separate aligned lanes: 13F institutional positions, 13D/G reported stakes, N-PORT fund exposure and national voting-rights/control disclosures. Use a quarter-by-quarter holder rank-flow/alluvial view for 13F change and a dated stake timeline for control filings; annotate filing lag, amendments, denominator and reported-vs-calculated basis. Add coverage/unknown remainder and never sum overlapping ownership systems.


**Backend**
- [ ] `P3.1` Extend 13F coverage from held CUSIPs to prioritized watchlist names/managers, including amendments, confidential-treatment indicators and manager identity changes. Reconcile totals, option put/call and voting/investment-discretion fields; do not call a 13F position a beneficial stake.
- [ ] `P3.2` Ingest Schedule 13D/G and amendments for the pilot issuers. Capture filer and group members, class, shares/percentage, reported voting/dispositive power, purpose of transaction, agreement/exhibit links and measurement/filing times. Link later amendments and compute a *reported* stake delta only for the same class and comparable basis.
- [ ] `P3.3` Pilot public N-PORT holdings for matched US funds, report date, filing date, securities and derivatives. Compare to issuer fund files; attribute differing dates, share classes, cash and derivatives. Never combine a fund holding with its manager's 13F as if they were two independent owners.
- [ ] `P3.4` Build national major-holder adapter contracts (UK TR-1 first, then BaFin/AMF/AFM/CONSOB, Japan EDINET, Canada SEDAR+ and ASX). For each, record disclosure threshold, voting rights versus economic interest, direct/indirect chain, denominator and publication lag. Enable one jurisdiction only after 20 reviewed examples meet P0 quality.
- [ ] `P3.5` Use Companies House PSC/GLEIF to corroborate legal-entity control and hierarchy, explicitly as separate control data. Keep a conflict review when listed-issuer beneficial ownership and corporate consolidation appear inconsistent.
- [ ] `P3.6` Add company ownership view: 13F manager trends, reported 13D/G control stakes, fund holdings, sources/as-of dates, top changes and an unknown remainder. Add watchlist alerts for a verified major-holder filing or material stake amendment; avoid interpreting quarter-to-quarter 13F movement as trading intent.
- [ ] `P3.7` Test 13F amendment replacement, same-manager name changes, CUSIP reuse, ADR/ordinary class, group aggregation, conflicting share denominators, N-PORT lag and non-US threshold differences. Sample manually before publishing any “top owners” or control alert.


### Implementation status and open gaps after P2–P3 (updated 2026-10-07)

**Built and tested** (backend `steward/disclosure/financials.py`, `statement_read.py`, `ownership.py`, `ownership_read.py`; migration `20261007110000_ownership_known_at.sql` + rollback; UI at `/disclosures` (statements) and `/disclosures/ownership`; tests `test_disclosure_financials.py`, `test_disclosure_statements_db.py`, `test_disclosure_ownership.py`, `test_disclosure_ownership_db.py`, web `financials.test.ts`, `ownership.test.ts`):

- P2.1 versioned metric taxonomy (`metrics-1`) and period classification; P2.3 segment bridge (axes never added, eliminations kept, remainder shown) and recast detection; P2.4 validators for debt maturities, leases, repurchases, customer shares; P2.6 adapter contract and the "no global label until measured" rule; P2.7 as-filed history with restatement overlay, derived free cash flow with inputs and formula version, valuation gate; P2.8 tests for restatement, changed taxonomy, negative cash flow, unit multiplier, fiscal-year change, missing note.
- P3.1 13F amendment replacement as of a date (RESTATEMENT replaces, NEW HOLDINGS adds, untyped amendment flagged), options kept out of share totals, exits visible, rename keyed by CIK; P3.2 reported-stake delta only for the same class; P3.4 national adapter contracts and the 20-example, 98% enablement gate; P3.5 registry-versus-filing conflict rule; P3.6 ownership lanes with a stated denominator, per-lane remainder and no combined total; P3.7 tests for amendment replacement, rename, CUSIP reuse, group floor, conflicting denominators, fund lag, non-US thresholds.
- `known_at` added to 13F, 13D/G and N-PORT filings (end of filing day UTC, never earlier). `confidential_treatment` on 13F filings is `unknown` until a source says otherwise.

**Gaps** (not done; later phases must not assume them):

| Gap | Task | Why it is open | What closes it |
|---|---|---|---|
| Statement checks cover only gross profit and free cash flow | P2.8 | `statement_read` runs two arithmetic checks; maturity, lease and repurchase validators exist but no stored data feeds them | Extract note tables into structured buckets (below), then call the validators from `statement_read` |
| Debt maturities, leases and repurchases are not extracted | P2.4 | Notes are stored as text blocks only; the UI shows sample maturities and an empty state for live data | Table extraction from note text blocks with evidence offsets, sent to review when ambiguous |
| Evidence has no span offsets for statement facts | P2.2 | `financial_statement_fact` keeps a document-level quote; it is not mirrored into `disclosure_observation` | Mirror facts into the foundation tables with `legacy_table`/`legacy_key` and offsets from the iXBRL parser |
| ESEF statements are partial and not reconciled to 20-F | P2.5 | Existing ESEF path stores some facts; no cross-listing comparison or conversion-source record | Pilot on the European corpus after ESEF discovery exists (P1 gap) |
| Segments use single-member contexts only | P2.3 | The existing `segment_revenue` skips cross-tabs; the new bridge skips them too and says so | Cross-axis tables as their own breakdown, never placed on one axis |
| No segment assets/profit/capex, geography overlap not tested on real filings | P2.3 | Only revenue is bridged | Extend the bridge to the other segment metrics |
| Valuation, screener and ROIC consumers do not use the gate | P2.7 | `usable_for_valuation` is returned; no consumer reads it yet; margins/ROIC recompute with citations is not built | Wire `/holdings/{ticker}/financial-history` and screens to the gate; add the formula table |
| EDINET, SEDAR+ adapters are contracts only | P2.6 | No discovery or fact extraction; access is unproven (see P0 gaps) | Implement after P0.4 access results |
| 13F reconciliation of totals not done | P3.1 | Only portfolio CUSIPs and tracked managers are stored, so a report total cannot be reconciled; a manager's report with none of the ticker may not be stored at all, in which case an exit is invisible | Store a per-filing row count and reported total; keep filings that list none of the held CUSIPs |
| Confidential-treatment flag never populated | P3.1 | Collector does not read it | Parse cover page flag and omitted-holdings notices |
| Manager lane is the latest report per manager, not a single quarter | P3.1 | The lane note says when entries mix report dates; no completeness rule picks one common quarter | Choose the latest quarter with enough managers reported and show earlier ones as "not yet filed" |
| 13D/G filing history per holder is not on the ownership page | P3.2 | The page shows the latest filing per holder; the chain view is sample-only because no endpoint returns the chain | Add a chain endpoint from `control_stake_filing.prior_accession` |
| Group members, purpose of transaction and exhibits not structured | P3.2 | Only the largest reporting person and Item 4 text are stored | Parse group members and agreement exhibits |
| N-PORT lane untested end to end | P3.3 | `ownership_read` reads confirmed N-PORT filings; the DB test covers 13F only (needs the instrument table seeded) | Add a DB test with a confirmed fund; compare against issuer fund files |
| National voting-rights lane is `not_covered` for every issuer | P3.4 | Contracts exist for UK, DE, FR, JP, CA, AU; no collector, and no jurisdiction has the 20 reviewed examples | Build UK TR-1 first, collect 20 examples, review, then enable |
| Companies House PSC / GLEIF control corroboration not collected | P3.5 | Only the conflict rule exists | Collect PSC and GLEIF hierarchy as separate control data |
| No ownership alerts and no manual sample before "top owners" | P3.6, P3.7 | Alerts for a verified major-holder filing are not built; the 50-match adjudication sample from the release thresholds is not taken | Build alerts after the sample passes |
| Python and TypeScript copies of the same rules | P2.8, P3.7 | Sample mode replays in TypeScript so the screen works offline; the Python module is the reference | Keep both suites in step; remove the TypeScript copy once live data exists everywhere |
| New pages not viewed in a browser | P2, P3 frontend | Sign-in needs MFA; logic and types are tested, layout is unseen | Screenshot pass at desktop and phone width; keyboard pass on the date control |

## Phase P4 — Insider economic activity

**Dependency:** P1; existing Forms 3/4/5 pipeline. **Exit:** a reader can distinguish discretionary open-market activity from mechanical compensation and tax events.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Use a transaction event strip around filing dates, with distinct visual encodings for open-market P/S, awards, option exercises, tax withholding, gifts and unknown activity. Add a zero-centered diverging net-purchase chart by role and 30/90/365-day window only for classified discretionary transactions; show gross transactions, linked exercise-sale bundles, post-trade ownership and plan/derivative caveats in an evidence-linked detail table. Do not imply motive.


**Backend**
- [ ] `P4.1` Backfill existing US insider rows with exact filing/evidence IDs, issuer/insider identifiers, officer/director/10% owner roles, transaction date versus filing date, direct/indirect ownership, security class, price, shares, post-transaction holdings and amendment lineage.
- [ ] `P4.2` Parse Form 4/5 footnotes and derivative table links, with referenced conversion/exercise price and expiry when disclosed. Retain original transaction code and reported 10b5-1 or non-10b5-1 plan indicator where the form provides it; never infer a plan from recurring dates alone.
- [ ] `P4.3` Implement a documented economic classifier: open-market `P` purchase, `S` sale, `M` option exercise, `A` grant/award, `F` tax withholding, `G` gift, and other/unknown. Treat linked exercise-and-sale bundles together; discretionary net buying excludes grants, gifts, taxes, transfers and unknowns. Let reviewers override a misclassified filing with reason.
- [ ] `P4.4` Pilot UK PDMR disclosures from FCA/issuer announcements; map actor, instrument, venue, nature, volume, price and notification date. Add further jurisdictions, including Canadian and Japanese sources, when retrieval, taxonomy and reviewer accuracy pass P0 thresholds.
- [ ] `P4.5` Build insider analysis: open-market net shares/value by role and 30/90/365-day window, clusters of independent insiders, ownership after trade, market-cap/float context and plan/derivative caveats. Link to dilution, buybacks and earnings/deal windows without assigning motive.
- [ ] `P4.6` Alert only on material verified open-market activity with deduplication by filing/amendment. Test corrections, split-adjusted shares, zero-price grants, derivative conversion, option exercise plus same-day sale and indirect trust holdings against reviewed examples.


## Phase P5 — Customer, supplier and revenue-dependency network

**Dependency:** P1/P2 and existing `news/regulatory.py` suggestions. **Exit:** every confirmed edge has a cited passage, direction, dates and named/anonymous status.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Use a directional, time-aware dependency graph for named entities and a separate concentration view for reported dependencies: ranked bars for named/anonymous customer shares, with an explicit unknown remainder, plus a portfolio exposure Sankey for confirmed edges only. A bounded scenario view may propagate only numeric disclosed/owner-assigned weights, show coverage at each hop and lower/upper bounds, and label qualitative edges as non-quantified. Suggested edges need a review state and source passage.


**Backend**
- [ ] `P5.1` Audit all existing SEC keyword-generated relationship suggestions. Fetch their exact passages and label true relationship, historical-only, hypothetical, negated, unrelated third party or ambiguous. Establish a precision baseline; do not auto-promote prior suggestions.
- [ ] `P5.2` Build structured-first extractors for 10-K/20-F/10-Q/6-K customer concentration, segment notes, material agreements and exhibits; then issuer releases/IR and procurement notices. Capture section and paragraph/table offsets, contract term, exclusivity, minimum purchase and termination if actually disclosed.
- [ ] `P5.3` Represent named buyer/supplier, anonymized “Customer A” or “three customers,” and industry/geography-only exposure as different claim shapes. Store reporter, counterparty, economic direction, product/service, fiscal period, revenue or purchases percentage/amount and denominator. Do not assign an anonymous label to a guessed real entity.
- [ ] `P5.4` Resolve named entities through CIK/LEI/registered name, dated aliases and subsidiaries. Require a reviewed entity match where two brands or affiliates overlap; document whether the edge is at issuer, subsidiary or business-unit level. Reconcile disclosures from both sides when possible and retain contradictions.
- [ ] `P5.5` Add TED award adapter for targeted issuer/legal-entity aliases. Parse buyer/supplier, award or lot ID, CPV, award value/currency, start/end and status. Deduplicate corrections and multiple lots; classify procurement as a contract signal, not a reported issuer revenue share.
- [ ] `P5.6` Maintain time-bounded claims and a confirmed graph edge only after rule or reviewer validation. Measure relationship precision on at least 100 sampled claims including deliberate false-positive phrases. Surface source age and unverified status in all graph reads.
- [ ] `P5.7` Calculate customer concentration, largest disclosed dependency and portfolio network exposure using only reported percentages or clearly labeled owner-assigned weights. Keep an unknown remainder. Run a shock scenario through one or two confirmed hops, with dates and lower/upper coverage; avoid numerical propagation from qualitative-only edges.
- [ ] `P5.8` Add company network and portfolio concentration views, change alerts for new/ended material relationships, and a review affordance for suggested edges. A user can trace each edge and percentage to the exact filing paragraph or table.

### Implementation status and open gaps after P4–P5 (updated 2026-10-07)

**Built and tested** (backend `steward/disclosure/insiders.py`, `insider_analysis.py`, `dependencies.py`, `dependency_read.py`; routes `GET /disclosures/holdings/{ticker}/insiders`, `POST /disclosures/insiders/override`, `GET /disclosures/holdings/{ticker}/dependencies`; migration `20261007120000_insider_and_dependency_shapes.sql` + rollback; UI at `/disclosures/insiders` and `/disclosures/network`; tests `test_disclosure_insiders.py`, `test_disclosure_insiders_db.py`, `test_disclosure_dependencies.py`, web `insiders.test.ts`, `dependencies.test.ts`):

- P4.3 discretionary rule (open-market buy/sell, non-derivative, shares and price above zero), exercise-plus-same-day-sale bundles kept out of the net, reviewer override as an append-only ledger (reason of 10+ characters, decider, applies only from `decided_at`, original class kept). P4.5 net by role and 30/90/365 days (by transaction date, visibility by `known_at`), people counted once by reporting-owner CIK, 10b5-1 trades counted apart, filings with no plan flag counted and labelled "unknown", clusters of three independent insiders within 30 days, position change from post-trade holdings, caveats that state facts and no motive. P4.6 alert candidates only for confirmed, material, open-market trades, deduplicated, never for a failed collection; tests for amendment replacement at three cutoffs, split adjustment, zero-price grants, derivative lines, exercise-plus-sale, indirect rows.
- `known_at` on `insider_filing` (end of filing day UTC), so an amendment replaces its original only once known.
- P5.3 claim shapes (`named`, `anonymous`, `group`, `segment_only`) with a stated denominator and period; an anonymous or group claim cannot carry a name or company id. P5.5 a procurement award cannot carry a share or denominator. P5.6/P5.7 concentration per (basis, period) with the unknown remainder, a group figure as a floor that is not added, largest dependency, status resolved as of the date from `graph_status_log`, stale-source flag; shock scenario over one or two confirmed numeric hops with lower/upper bounds, coverage and a list of non-quantified edges. P5.1 rule-based passage screening (negated, hypothetical, historical, third party) as audit hints only, a reproducible audit sample, and a precision gate (100+ claims, planted false positives caught, 95%).

**Gaps** (not done; later phases must not assume them):

| Gap | Task | Why it is open | What closes it |
|---|---|---|---|
| Insider backfill and footnote parsing are not run | P4.1, P4.2 | Existing rows are read as stored; footnotes, derivative-table links, exercise price/expiry and amendment lineage beyond `superseded_by_accession` are not parsed | Re-parse stored Form 4/5 XML into the new fields, then re-run the classifier and spot-check |
| Classifier accuracy is unmeasured | P4.3 | The 50-classification manual sample (98% bar) has not been taken, so classes are labelled machine-derived | Sample 50 filings, adjudicate, record the result in the release-thresholds doc |
| Insider aggregation never run on the real database | P4.5 | Tests use a throwaway database with invented rows | Run the read against production data and compare a handful of tickers to the filings |
| Market-cap, float, dilution, buyback and earnings-window context missing | P4.5 | The view shows the insider's own position change only | Join share count (ownership denominator), buyback and earnings dates, as context labels without motive |
| UK PDMR pilot and other jurisdictions not built | P4.4 | No collector, no known-at rule for PDMR notices, no plan or amendment fields; Canadian and Japanese sources not assessed | Build the UK pilot after P0 access results; add jurisdictions only after the P0 thresholds pass |
| Insider alerts are not wired to the digest | P4.6 | `alerts()` returns candidates; nothing stores a "last alerted" mark or sends them | Persist the last-alerted accession set and call from the digest job |
| Splits are applied in the pure module only | P4.6 | `adjust_for_splits` is tested but the reader does not feed corporate actions | Pass confirmed split rows from `corporate_action` into `insider_activity` |
| Relationship audit not run on real passages | P5.1 | The audit tooling exists; no existing suggestion has been labelled, so the precision baseline is unknown | Pull the passages for all existing suggestions, label them, run `audit_precision` |
| Structured extractors not built | P5.2 | Existing extraction is the LLM quote path; no table or note extractor, no contract term, exclusivity or minimum purchase | Build table-first extraction from customer-concentration notes with offsets |
| `claim_shape`, denominator and period are backfilled coarsely | P5.3 | Old rows get `named` or `anonymous` from the anonymous flag; denominator and period stay null, so they are excluded from concentration until a reviewer or extractor sets them | Re-extract or review old claims to fill denominator and period |
| An anonymous label ("Customer A") is not stored | P5.3 | Existing check requires `counterparty_name` null for anonymous claims; the label lives only in the passage | Add a `claim_label` column and fill it from the passage |
| Entity resolution and two-sided reconciliation not wired | P5.4 | `side_conflict` is a stub (identical-relationship case only); no alias table, CIK/LEI match or reviewed-match gate | Add dated aliases and a reviewed match, then reconcile both sides with the contradiction kept |
| TED awards not shown in the new view | P5.5 | `graph/ted.py` collects awards; the view lists awards only if they are stored as `procurement_award` claims | Map collected awards (lot ID, CPV, value, dates) into claims or a sibling table |
| Scenario edges come only from named customers with a company key | P5.7 | The reader needs `counterparty_key` to link two companies; keys are rarely set | Resolve keys through entity resolution (P5.4); add owner-assigned weights labelled as such |
| No change alerts and no inline review | P5.8 | A new or ended material relationship raises nothing; the page links to Relationships for decisions instead of recording them itself | Diff claims between two dates for alerts; add a decision call on the claim |
| Portfolio exposure uses sample holdings or one holding | P5.7, P5.8 | No endpoint joins portfolio weights to confirmed edges | Add a portfolio endpoint returning weights and confirmed edges |
| Two sides of the 98% named-edge gate not measured | P5.6 | The gate is coded, not run | Sample 100+ confirmed claims with planted false positives, record the result |
| Python and TypeScript copies of the same rules | P4, P5 | Sample mode replays in TypeScript; the net and concentration are also recomputed in the browser from the backend's list | Keep both suites in step; remove the copies once live data exists everywhere |
| New pages not viewed in a browser | P4, P5 frontend | Sign-in needs MFA; logic and types are tested, layout is unseen | Screenshot pass at desktop and phone width, keyboard pass on the window toggle and override form |
| Rollback not run against a database with data | P4, P5 | The rollback drops the override ledger and new columns by design | Rehearse on a copy before applying to production |

### Gap-closure round for P0–P5 (updated 2026-10-07)

The gap tables above were written before this round. Where a row below is marked **closed**, trust this section over the row above. Migration `20261007130000_disclosure_gap_closure.sql` (+ rollback) adds `disclosure_alert`, `owner_dependency_weight`, `procurement_award_detail`, `control_stake_exhibit`, `disclosure_adjudication`, and columns on `relationship_disclosure` and `form13f_filing`.

**Closed in code** (tests: `test_disclosure_gap_closure.py` 38 pure, `test_disclosure_gap_closure_db.py` 21 DB, `test_disclosure_ownership_gaps.py`, `test_disclosure_note_tables.py`, `test_disclosure_pdmr_activity.py`, web `dependencies.test.ts`, `alerts.test.ts`):

| Gap | Task | How it is closed |
|---|---|---|
| Splits applied in the pure module only | P4.6 | `insider_activity` and `pending_alerts` restate shares for confirmed splits known by the as-of date |
| Footnotes, derivative terms, plan basis not shown; no share-count context | P4.2, P4.5 | Detail rows carry footnote, exercise price, dates, underlying, plan basis and percent of outstanding; a context block shows the share count and confirmed events, with no motive implied |
| Insider, large-holder and relationship alerts not stored | P4.6, P3.6, P5.8 | `disclosure_alert` with a unique `dedupe_key`; scans run after the insider collection; list and acknowledge routes; page at `/disclosures/alerts` |
| `side_conflict` a stub; no alias or entity resolution | P5.4 | `entities.py` (only a CIK or LEI match is automatic; names are candidates); real opposite-direction conflicts; other reporters’ claims used as inbound edges |
| Anonymous label not stored | P5.3 | `claim_label` column, backfilled from the passage |
| Denominator backfill | P5.3 | `denominator_kind` backfilled from `share_basis` where it maps |
| TED awards not in the view | P5.5 | `procurement_award_detail` (lot, CPV, value, currency, status) shown on the claim |
| Owner-assigned weights | P5.7 | `owner_dependency_weight`, labelled in scenarios, never mixed into reported concentration |
| No change diff, no inline review | P5.8 | `changes()` between two dates feeds the relationship alerts; decision form on each suggestion |
| Portfolio exposure uses sample data | P5.7, P5.8 | `GET /disclosures/portfolio/dependencies` (latest period per holding; holdings without a claim listed as unknown) and a live Sankey |
| No structured extractor | P5.2 | `concentration_extract.py` (sentence and table, offsets, negations to `skipped`) and `concentration_store.py` (stores as suggested) |
| Gates coded, not run | P5.1, P5.6, P4.3, P3.7 | `adjudication.py` plus routes and the page `/disclosures/adjudication` let a person label a sample and read the gate; the run itself has not been done |
| 13F report totals and confidential flag | P3.1 | Summary page line count, total and omitted-holdings flag stored; the confidential flag is read from the cover |
| Manager lane mixes quarters | P3.1 | One common quarter (the newest with 80% of managers filed); non-filers named; a fallback flag when none qualifies |
| No 13D/G chain; exhibits not parsed | P3.2 | Chain route and view per filer; Item 7 exhibits parsed to `control_stake_exhibit` |
| Note tables not extracted | P2.4, P2.8 | `note_tables.py` extracts debt, lease and repurchase tables with offsets; `note_store.py` stores buckets; validators run from `statement_read` |
| Segments: assets, profit, capex, cross-axis | P2.3 | Segment report per metric; cross-axis facts as their own breakdown; geography/product overlap noted |
| Formula table | P2.7 | Versioned `formula-table-1` (margins, ROIC, debt-maturity coverage, cash conversion), null with a reason when an input is missing |
| Valuation gate not exposed | P2.7 | `valuation_gate` on statements and on `/holdings/{ticker}/financial-history` rows |
| UK PDMR rules | P4.4 | `pdmr_activity.py`: known at the announcement time, role from position text, amendments counted once, no plan flag, totals per currency; route `GET /disclosures/holdings/{ticker}/pdmr`; a "UK manager dealings" panel on the insiders page |
| N-PORT lane untested end to end | P3.3 | DB test seeds a confirmed and an unmatched fund filing and checks the lane, the lag, and that the 13F lane is not added to it |
| Screener ignores the gate | P2.7 | Each screen row carries `valuation_gate`; a row with no issuer link or no gate for its period says so (null), never a pass. Filters are unchanged |
| Note tables never extracted from a filing | P2.4 | `note_job.py` reads a parsed filing's debt, lease and repurchase text blocks, takes the currency and period from the filing, and stores buckets at document offsets; `POST /disclosures/versions/{id}/extract-notes` runs it on an archived version. Nothing runs it on a schedule (see below) |
| `ownership_match` sampler | P3.7 | `ownership_sample` lists each CUSIP and ticker pair once; labels are same / different / cannot tell; added to the accuracy-check page |
| Baseline inventory | P0.5 | `python -m steward.disclosure.baseline` prints row counts, documents by regulator, suggestion states and the confirmed-link share. It only reads and has not been run against the deployed database |

**Still open — can be built:**

| Gap | Task | What is missing |
|---|---|---|
| Ingestion job is exposed as an owner route, not scheduled | P1.3, P2.2, P2.4 | `POST /disclosures/issuers/{id}/ingest` (SEC submissions then archive through `DisclosureFetcher`) and `POST /disclosures/identifiers/resolve` now call the jobs; no scheduler does, and neither route has run against sec.gov, GLEIF or OpenFIGI. `ingest_job.ingest_filings` fetches discovered SEC filings into `source_document`, parses them, extracts notes and mirrors facts, and refuses to run unless the `sec.archive` source is enabled with a recorded access test. No collector job or route calls it, and the real fetcher wiring has not been exercised against sec.gov |
| Identifier resolution is not scheduled | P0.2, P1.5 | `identifier_job.resolve_identifiers` stores GLEIF and OpenFIGI matches as suggestions only (exact ISIN or CIK agreement up to 0.95; name-only capped at 0.6 and flagged; ambiguous candidates each stored with a note). The GLEIF adapter resolves by ISIN only; a person still has to confirm each match |
| ESEF, EDINET and SEDAR+ are parsers only | P1.3, P2.5, P2.6 | `esef_discovery.py` and `jurisdiction_adapters.py` turn index and list responses into dated discovered filings (known_at never earlier than the stated time; skipped entries reported with reasons). They are written against assumed response shapes, unverified against the live services, and nothing stores or fetches with them |
| TR-1 notices are stored but not collected | P3.4 | `sources/tr1_uk.py` parses the standard form and `tr1_store.py` writes it to `uk_tr1_notice` (migration `20261007140000`). The template wording is unverified against live RNS, nothing fetches RNS bodies, and the national lane stays `not_covered` until 20 reviewed examples reach the bar |
| PSC and GLEIF corroboration has no data or route | P3.5 | `control_corroboration.py` compares PSC bands and a GLEIF parent with a reported stake (agree, conflict kept for review, not comparable). Nothing collects PSC or GLEIF hierarchy into it, and no route shows it; the PSC strings and band edges are unverified |
| No other-jurisdiction PDMR collector | P4.4 | UK only, and only the notices already parsed |
| TED field names | P5.5 | `classification-cpv` and `identifier-lot` are not verified against the live TED API |
| Alerts not sent to the digest | P4.6 | Alerts are stored and shown on a page; no push or digest line yet |

**Live rollout, 2026-10-07 (the deployed database):** the six migrations `20261007100000` to `20261007150000` were applied to the StewardAI project (Supabase records them under timestamp versions, not the file names). Facts found on real data:

- **Baseline taken (P0.5, read-only):** 53,354 source documents, 72,103 13F filings, 15,555 insider filings, 100 relationship disclosures (57 confirmed, 43 suggested), 24 of 30 instruments with a confirmed security link. The false-positive rate is still unmeasured: it needs a person's labels.
- **Access tests recorded (P0.4):** SEC submissions, archive, company facts and N-PORT bulk, ESEF, Companies House, GLEIF and TED pass; `sec.submissions` and `sec.archive` are enabled. `fca.nsm` fails (HTTP 400 "Invalid index": the index name in the probe is no longer accepted), so UK TR-1 retrieval is not demonstrated and nothing was done to find another route. EDINET is untested (no key); SEDAR+ needs a browser session.
- **Real-sample defect found and fixed (P0.3, P2.4):** real filings tag only a note's heading and carry the rest in `ix:continuation` elements chained by `continuedAt`; the reader saw only the heading and stored no note tables. After the fix 30 of 33 live filings have note tables (1,004 rows). The three without are two Boeing filings (the debt note is narrative) and one Meta filing (not diagnosed). This is a measured rate on real filings, not an accuracy figure.
- **Older filings had no evidence links:** the stored-fact mirror only reaches filings whose facts were copied into `financial_statement_fact`. `fact_evidence.mirror_document_facts` now reads the filing's own inline facts. Of the 33 stored filings, 20 are linked; 13 wait for the daily backfill (a manual catch-up was twice stopped by low memory on the host). One AstraZeneca filing yields 0 observations and is undiagnosed.
- **Automated now:** `disclosure_sources` (daily source tests, auto off on failure, SEC sources auto on), `disclosure_ingest` (weekdays, plus a five-filing backfill step), `disclosure_identifiers` (weekly), `disclosure_alerts` (daily scan and e-mail). Still human by design: confirming identifier suggestions, labelling samples, the national-lane enablement bar.

**Still open — cannot be closed from code:** real sample capture (P0.3), measuring any threshold on production data (P0.6, P3.7, P4.3, P5.6), the deployed-database baseline (P0.5), live checks of the FCA, EDINET, SEDAR+ and TED routes (P0.4), running the aggregation against real tickers (P4.5), the human adjudication runs, rehearsing the rollbacks on a database with data, and the browser pass over every new page (sign-in needs MFA; layout is unseen).

## Phase P6 — Public deal, financing and corporate-action terms

**Dependency:** P1/P2/P5. **Exit:** material public transactions have an evidence-linked lifecycle and quantified portfolio implications where terms permit.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Render deals as a lifecycle timeline with state transitions, dated term versions and filing links; pair it with a consideration bridge (cash/stock/debt/earnout) and a scenario waterfall for supportable portfolio or dilution impact. Distinguish announced terms, management projections, owner scenarios and unknown terms; show unaffected-price date and state evidence on hover/detail.


**Backend**
- [ ] `P6.1` Define an event taxonomy: acquisition, merger, divestiture/asset sale, IPO, follow-on, debt issuance/refinancing, spin-off, joint venture, strategic investment, buyback, tender offer and material commercial agreement. Define required/optional fields and state transitions per type.
- [ ] `P6.2` Discover SEC 8-K/6-K items and cited exhibits, S-4/proxy/registration statements, 10-K/10-Q subsequent-event notes and issuer notices; cluster documents by parties, announced date and transaction identifier. Add non-US announcement adapters only after the P0 gate.
- [ ] `P6.3` Extract announced price, cash/stock mix, exchange ratio, stake, financing and debt, earnout, closing conditions, termination fee, expected synergies and announced dates when stated. Store a typed unknown for absent terms; never fill gaps from an unsourced headline.
- [ ] `P6.4` Implement a deal state machine: rumored (only if explicitly sourced and labelled), announced, definitive, approved, closed, terminated and contested. Evidence and known-at date are mandatory for each transition. Amendments create new term versions; a terminated deal remains in history.
- [ ] `P6.5` Reconcile parties to corporate structure and share classes; connect a closed deal to successor names/listings and reported financial consolidation dates. Distinguish legal closing from first included reporting period and from portfolio broker corporate-action posting.
- [ ] `P6.6` Compute only supportable impacts: announced premium using a correctly dated unaffected price, cash/stock dilution under stated exchange ratio, debt/EV change, segment mix and portfolio exposure. Label owner scenarios and management-projected synergies separately; rerun calculations when terms change.
- [ ] `P6.7` Build a deal timeline, transaction-comparison filters and event alerts on holdings/watchlist. Show participant, state, latest terms, original and amended documents, scenario impact, confidence and explicit missing fields.
- [ ] `P6.8` Test a withdrawn deal, multiple bids, cash-plus-stock consideration, spin-off share distribution, buyback authorization versus actual purchases, large debt refinancing and same-party simultaneous asset deal. Manually verify amounts/currencies/units and state chronology.


## Phase P7 — Public consensus, revisions and segment estimates

**Dependency:** P1/P2; free data plan D4.5/D4.10. **Exit:** a small historical issuer consensus set produces reproducible actual comparisons and compatible revision history.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Use estimate range/fan charts by fiscal period with mean/median/high/low and panel size, then revision slopegraphs across only comparable snapshots. At earnings, pair pre-release estimate with reported actual and a clearly defined surprise bridge; segment estimates get aligned small multiples with taxonomy/recast breaks marked. Put published consensus, named analyst estimates, company guidance, event-calendar estimates and owner scenarios in separate lanes.


**Backend**
- [ ] `P7.1` Create issuer IR consensus registry: URL/discovery method, publisher, whether values originate from third-party brokers, accessible format, authentication, update cadence and sample files. Use at least two issuers with reliably retrievable historical versions for the pilot; record missing files or incompatible definitions as coverage gaps, not licensing blocks.
- [ ] `P7.2` Fetch accessible HTML/PDF/XLSX files with version hashes and publication timestamps. Extract headings, footnotes, panel size, fiscal-year/quarter definitions, currency, reported versus adjusted basis, mean/median/high/low and metric/segment taxonomy. Archive source versions for historical replay.
- [ ] `P7.3` Keep `company_guidance`, `published_analyst_consensus`, `named_individual_estimate`, `owner_scenario` and `event_calendar_estimate` separate. Record if the publisher supplies consensus only for selected metrics or brokers. A management target never becomes an analyst estimate; a vendor event estimate is not a full historical consensus panel.
- [ ] `P7.4` Implement revisions only between comparable snapshots: same issuer, metric, scope/segment, period, unit, currency, accounting basis and comparable panel methodology. Calculate absolute/percentage revision, elapsed days, analyst-count change if available and direction; flag method/panel breaks rather than a numerical “revision.” Do not interpolate missing days.
- [ ] `P7.5` Where an issuer publishes segment consensus, map each segment definition to reported segments for that fiscal period. Preserve recasts and non-additive/elimination lines. Compute actual-versus-consensus by segment with a cited bridge; missing segment estimates stay missing.
- [ ] `P7.6` Capture individual broker estimates from any free accessible source that names the analyst/broker, metric, period and publication time, including an owner-provided source if available. Preserve version history and source identity. If no source returns the required data, show a technical/coverage gap; never infer analyst-level values from a consensus mean/range.
- [ ] `P7.7` Build company estimate timeline and event comparison: prior/latest accessible snapshot, revision markers, reported actual, surprise formula, panel-size/basis warnings and linked IR file. Use estimate snapshots known before earnings release; a later archived file cannot be used to score the earlier surprise.
- [ ] `P7.8` Test fiscal-calendar roll, company restatement, changed adjusted definition, currency restatement, panel replacement, segment recast, missing analyst count and corrected IR file. Review at least 20 snapshot pairs before enabling revision alerts or factor-like screens.


### Implementation status and open gaps after P6–P7 (updated 2026-10-07)

**Built and tested offline** (migrations `20261007160000_deal_events.sql`, `20261007170000_public_estimates.sql`, `20261007180000_deal_state_alerts.sql` + rollbacks; backend `deals.py`, `deal_extract.py`, `deal_read.py`, `estimates.py`, `estimate_extract.py`, `estimate_read.py`; routes `GET /disclosures/holdings/{ticker}/deals`, `GET /disclosures/portfolio/deals`, `GET /disclosures/holdings/{ticker}/estimates`; UI at `/disclosures/deals` and `/disclosures/estimates`; tests `test_disclosure_deals.py`, `test_disclosure_estimates.py` (pure, passing), `test_disclosure_deals_db.py`, `test_disclosure_estimates_db.py` (written, **not run**), web `deals.test.ts`, `estimates.test.ts`):

- P6.1 taxonomy and P6.4 state machine (every transition needs a dated document and `known_at`; a rumoured state needs a stated source; illegal transitions are rejected with a reason; terminated deals stay in history). P6.3 extractor with offsets and typed "not stated" terms; negated and hypothetical wording skipped. P6.2 document clustering (pure). P6.5 legal closing, first reporting period and broker posting kept as three dates. P6.6 premium against a correctly dated unaffected price, dilution from a stated exchange ratio, consideration bridge (unstated terms stay open, never zero), debt/EV change, segment mix, spin-off, buyback; management projections and owner scenarios carry their own origin. P6.7 `deal_state` alerts for a documented transition on a held name (never for a rumour), shown on `/disclosures/alerts`.
- P7.1 consensus-source registry. P7.2 offline HTML, spreadsheet-row and PDF-text extractor with version-hash dedupe. P7.3 five series kept apart by constraints (a guidance row cannot be stored as consensus). P7.4 revisions only between comparable snapshots; a break (basis, currency, panel method, period, unit, restatement) carries its reason and no number. P7.5 segment bridge with recasts and non-additive lines kept. P7.6 named-estimate coverage gap, never derived from a mean or range. P7.7 surprise against the latest snapshot known strictly before the release. P7.8 pure tests for the listed cases. Revision-alert gate: 20 reviewed pairs at a 90% bar (`estimate_pair_review`); nothing is registered with alerts.

**Gaps** (not done; later phases must not assume them):

| Gap | Task | What closes it |
|---|---|---|
| DB tests for deals and estimates never ran, so the migrations and read SQL are untested against Postgres | P6, P7 | Run both `*_db.py` files against a disposable database in CI |
| No discovery or ingest job writes `deal_*` rows (8-K/6-K/S-4/proxy discovery); extractor and clustering are pure functions | P6.2 | Wire discovery through the fetch governor, then store transitions, term versions and documents |
| Share counts, enterprise value and segment revenue are not read from stored observations, so dilution, debt/EV and segment-mix impacts are null with a reason in live mode | P6.6 | Read P2 observations at the as-of date |
| Party-to-issuer matching is manual (`issuer_id` stays null until reviewed); extractor is English and US-format only; no non-US adapters | P6.5, P6.2 | Reviewed match through entity resolution; add adapters after the P0 gate |
| Deals page filters only by type and state (size and currency exist in the API); the TypeScript sample omits segment mix, spin-off and tender impacts | P6.7 | Add the filters; keep sample and backend rules in step |
| No collector wires the fetch governor to the estimate extractor, so no snapshot, evidence row or corrected-file link is stored | P7.2 | Scheduled job and a test over the stored fixture |
| No real pilot: two issuers with retrievable historical versions are not seeded | P7.1 | Register and capture two issuers' files |
| The 20 snapshot-pair reviews are not done and no screen records them | P7.8 | Review UI over `estimate_pair_review`, then enable revision alerts |
| No named-individual-estimate source exists; the lane states the gap | P7.6 | A free source or owner-provided access |
| Estimates page shows only the first period of a segment bridge; the restated flag is not in the sample logic | P7.5 | Period switcher; carry the flag into the TypeScript copy |
| Actuals are read from `disclosure_observation` only; otherwise "actual not loaded" | P7.7 | Statement facts for the pilot issuers (P2 ingest) |
| New pages not viewed in a browser | P6, P7 frontend | Sign-in needs MFA; logic and types are tested, layout is unseen. Screenshot pass at desktop and phone width; keyboard pass on timeline, filters and lanes |
| Python and TypeScript copies of the same rules | P6, P7 | Keep both suites in step; remove the copies once live data exists |

### Consolidated open-gap register, P0–P9 (updated 2026-10-07)

One place to read what is not done. The per-phase status sections above hold the detail; where they disagree with this register, trust this register (it was written last). Each row says what kind of work closes it.

**A. Nothing has run against real data or a real service** (cannot be closed from code)

| Gap | Tasks | Close it by |
|---|---|---|
| 0 of 110 P0.3 samples captured; release thresholds (P0.6) unmeasured | P0.3, P0.6 | Pull 10 per category through the fetch governor, hand-check, then sample against `public-disclosure-release-thresholds.md` |
| Deployed-database baseline and false-positive rate not measured | P0.5 | Run `python -m steward.disclosure.baseline` on the deployed database |
| Live routes unproven: SEC FSN dataset URL, FCA request shape, EDINET key, SEDAR+ session, TED field names (`classification-cpv`, `identifier-lot`) | P0.4, P5.5 | Record each as tested or failed in the source registry |
| Ingest, identifier-resolution and TR-1 collection never ran against sec.gov, GLEIF, OpenFIGI or RNS; wording and response shapes are assumed | P1.3, P1.5, P3.4 | Run each once on the deployed host and fix what differs |
| Every accuracy gate coded but not run: 100 relationship claims, 50 ownership matches, 50 insider classes, 30 deal states, 20 revision pairs, 30 note values | P3.7, P4.3, P5.1, P5.6, P6.8, P7.8, P9.3 | Human labelling through `/disclosures/adjudication`; record numerator and denominator |
| Insider aggregation never run on production tickers | P4.5 | Compare a handful of tickers to the filings |
| No migration (14 files since `20261007100000`) or rollback ever rehearsed on a database with data | P1.1 to P7 | Rehearse the full up/down chain on a copy |
| **No new page has been viewed in a browser**: `/disclosures`, `/ownership`, `/insiders`, `/network`, `/alerts`, `/adjudication`, `/deals`, `/estimates`, `/screen`, `/coverage`, `/replay` | all frontend | Sign in with MFA, one screenshot at desktop and phone width per page, keyboard pass on each date control, toggle, form, timeline and lane |
| P9.2 30-day shadow collection not started; incremental coverage, request volume, parse failures and match precision unmeasured | P9.2 | Run the scheduled jobs for 30 days on held and watchlist issuers, then read the coverage report |
| P9.6 backup/restore, retention and deletion by source, reviewer audit, RLS audit, secret handling, provider shutdown and rollback never exercised | P9.6 | Run each drill on a copy of the database and record the result |
| Release order (SEC/PIT, ownership/insiders, relationships/deals, estimates, other jurisdictions) is listed in the coverage report but nothing enforces it | P9.7 | Gate each page on its dataset's adjudication gate and the coverage report |

**Update 2026-10-07 (gap-fill round).** Against a disposable Postgres 17 (container on port 54329, not the CI pgmq image), 13 disclosure DB test files plus the new `test_disclosure_alerts_db.py` ran: all pass. That run found and fixed one real bug (`alerts.scan_deals` selected a column that does not exist, so deal alerts could never fire). The adapter sweep (`test_disclosure_sweep.py` and five new cases in `adapter_contract_cases.py`) found and fixed another (`parse_number` read `1,2,3` as 123). `mypy` (the repo's checker; pyright is not installed) is clean on `steward/disclosure` and `api` after fixing 21 errors. Full web suite: 939 tests pass. Built in this round, so the rows below that name them are closed: `company/{ticker}/…` aliases, estimate pair-review endpoints and panel, segment-period switcher, restated flag in the API and TypeScript copy, deals size/currency filters, `/disclosures/look-through` page, unread-alerts panel on the holding page. Still open from this round's list: deal impacts read from observations, 13F and watchlist in the coverage report, DB tests for controlling-stake / insider-cluster / estimate-revision scans and for the point-in-time reads, split/FX/share-class portfolio math, model-phrased answers, scheduler wiring, redo of the P8 pages with the frontend-design skill, and every item in block A.

**Update 2026-10-07 (second gap-fill round; this block overrides the rows below where they differ).** Built: `disclosure_estimates` job (cron `30 6 * * 1-5`, registered in `config/jobs.py`, `config/budget.py`, `jobs/registry.py` and the web jobs form) backed by `estimate_store.py` (snapshots known when first seen, same bytes stored once, re-issued date supersedes); 13F and watchlist coverage (`include_watchlist`, `watchlist_only`); deal impacts now read shares outstanding and net debt from stored facts (enterprise value and segment revenue are still null); `answer.phrase`, a model rewrite kept only if `numbers_grounded` passes, with tests (a fake model: grounded, invented number, unconfigured, failure); tests for split/FX/share-class portfolio math, DB tests for insider amendment and deal reversal through the point-in-time reads, for the controlling-stake and insider-cluster scans, and for the estimate store. Fixed: duplicate OpenAPI operation IDs from the `company/{ticker}/…` aliases (the aliases are now hidden from the schema; the web client uses `holdings/{ticker}/…`), the budget-site registry missed the disclosure fetcher (`sec.archive`, 600 requests a minute), and a test read source files with the Windows code page. Four scheduled jobs already existed before this round (ingest, identifiers, alerts, sources), so the "no scheduler" row below is stale. Still open: `phrase` is not wired into the research-answer route (it needs a gpt-*/gemini-* chat model and budget governance; Claude models are disabled by config); deal discovery job (8-K/S-4/proxy); estimate-revision scan DB test; issuer-rename and restatement point-in-time tests; ESEF/EDINET/SEDAR+ fetchers; the P8 page restyle (needs a browser); block A.

**Update 2026-10-07 (third gap-fill round; this block overrides the rows below and the second update where they differ).** Built: the `disclosure_deals` job (`deal_job.py`, weekdays 06:50) reads recent 8-K/6-K/S-4/proxy/tender-offer filings of held issuers and stores the deal, its state (1.02 terminated, 2.01 closed, 1.01 or a merger proxy definitive, else announced), participants (roles only from the sentence, never matched to an issuer) and a term version when a stated figure changed, each tied to its passage; a filing with fewer than two parties or no deal type stores nothing and is counted. The `disclosure_esef` job (`foreign_job.py`, weekdays 07:00) stores each new ESEF report of a held issuer with a confirmed LEI as a document version known when the registry processed it. EDINET list rows are now also stored as dated document versions from the response the existing `edinet_filings` job already fetches (no extra credit). `answer.phrase` is connected: `POST /disclosures/answer` takes `phrase: true` (a checkbox on the replay page), uses the chat model from the jobs config, and falls back to the template when that model is Claude (disabled), unconfigured, failing or its text has a number the evidence lacks; `phrased_by` says which was shown. Deal impacts now read enterprise value (closing price on or before the announcement times shares, plus net debt, only when price, debt and cash share one currency) and segment revenue (latest period with segments, per participant). Tests: DB tests for the deal job, the foreign job and the deal collector, the estimate-revision scan (unmet gate opens a review, met gate alerts once), issuer rename and succession read as of a date; restatement as of a date was already covered in `test_disclosure_db.py`. Still not built: a SEDAR+ fetcher (it needs a browser session and has no public list or export; its parser takes a file a person exports), reading values out of ESEF or EDINET documents into observations (ESEF statements still arrive through the statement pipeline), deal party-to-issuer matching (a reviewer's step), the P8 page restyle (needs a browser), and block A.

**Update 2026-10-08 (production run and drills; this block overrides the rows below and the three updates above where they differ).** Run against the StewardAI Supabase project with the owner's secrets: 4 migrations applied; `disclosure_ingest` stored 37 filings; `disclosure_esef` stored 14 ESEF reports (ASML, AstraZeneca; the live index names the entity only by link and by `fxo_id`, and a 404 means "no ESEF entity", now counted as coverage not failure); the alerts job stored 111 alerts (110 insider trades, 1 relationship change) and mailed 100; `disclosure_deals` read 13 filings and stored 0 deals (9 had unclear parties, 4 no deal type); estimates stored 0 (no consensus source has a URL); `control_stakes` found 148 candidates, 0 new. AstraZeneca's Companies House number (02723534, from GLEIF `registeredAs`, authority RA000585) is saved in the holding profiles (config version 57); the collector still needs the free API key. N-PORT has nothing to collect for this portfolio: the three held funds are IE/JE-domiciled and file no N-PORT.
- **Accuracy gates (model-adjudicated, not a person; see `docs/data/public-disclosure-release-thresholds.md`).** Relationship claims 86 of 93 real (92.5%, bar 95%, 100 needed), 7 of 7 planted caught: not met. Insider classes 54 of 55 (98.2%): meets the bar on the model's reading. Ownership matches 24 of 24, but only 24 pairs exist (50 needed): not met. Deal states, revision pairs and note values: nothing to sample yet. None counts as a release pass until a person reads the sample.
- **Insider aggregation (P4.5) checked.** Six held tickers (NVDA, KO, CELH, AMD, META, GOOGL), 90 and 365 days, recomputed from raw rows: the API totals equal the raw P and S shares after the stated exclusions (plan-trade sales, bundled sales, rows the classifier reads as non-open-market). One Form 4 (CELH, 2026-09-14 and 15) was re-read from EDGAR: code, date, shares and price agree. 20 sampled Form 4 rows (10 purchase and sale, 10 non-discretionary) all agree with their original XML (hash recorded).
- **P0.3 manifest.** 30 slots checked by the model against the source (10 + 10 Form 4, 10 SEDAR+), 20 marked unavailable (N-PORT, consensus), 60 pending; `captured` stays 0 because a person has not read any slot.
- **SEDAR+ (browser).** Public search sits behind a hCaptcha bot check a person must pass; after that the results table gives profile number, document name, submit time to the second with its zone, jurisdiction and size, a "Generate URL" action and an Export button, but no document id (result links are session tokens). `sedarplus_page_rows` reads those rows (zone read as stated, derived id marked `derived`), `collect_sedarplus_page` stores rows of held profiles only (none of the 30 holdings is Canadian, so nothing is stored). There is no unattended fetcher; the CSA Data Distribution Service is the route to evaluate for bulk.
- **P9.6 drills on a local Postgres 17 copy** (the Supabase CLI is not installed, so the copy is a plain container with the repo's Supabase shim): fresh schema from the migration files plus every production row (165 tables, 1,573,805 rows, 6 minutes): row counts equal production. The 11 down migrations ran in order and the 11 up migrations re-ran on the data with no error; rollback changed no surviving table and, as its headers say, dropped the 24 new tables with their rows (including 10,765 observations and 111 alerts). Row-level security is on, with policies, for all 165 tables, and the production security advisor lists no table without it. Sixteen tables carry the append-only guard and delete attempts on all six populated ones were refused. With the SEC sources disabled the ingest job ends its step with the named reason and does not crash. A secret scan of tracked files finds only a fake password in a redaction test; `.env` files are ignored. **Defects found:** (1) production and the migration files disagree: production holds a `price_bar` row (source `issuer`) that the migration's `price_bar_source_check` refuses, and `hs_concordance` has two production columns (`source_sha256`, `source_url`) no migration creates; (2) there is no delete-by-source routine: deleting the ESEF documents is blocked by the version foreign key and by the append-only guard, so a source cannot yet be withdrawn; (3) `disclosure_adjudication` is not append-only: a second label overwrites the first and only the time changes; (4) the advisor warns that two functions have a mutable search path (`private.screen_version_immutable`, `public.ownership_known_at_default`) and that `public.rls_auto_enable()` is executable by `anon` and `authenticated`; (5) four migration rows in production carry today's timestamps rather than their file versions (the alignment update was denied; run `supabase/manual/reconcile_migration_history.sql` after review).
- Still open after this: a person's read of every adjudication sample and manifest slot; the 30-day shadow run; the browser pass over the 11 pages and the P8 restyle; reading values out of ESEF and EDINET documents; deal party matching; EDINET and Companies House keys; consensus sources; the 13F amendment, 13D/G, 8-K deal, XBRL note, ESEF note and relationship manifest slots; fixes for the five defects above.

**Update 2026-10-08 (fixes round; overrides the "still open" line above where they differ).** Built on the `quant` branch, tested on the local container, **not yet applied to production** (three new migrations: `20261008100000_defect_fixes`, `20261008110000_adjudication_suggestions`, `20261008120000_deal_party_decisions`, each with a rollback).
- **Relationship false positives.** A one-word TED winner ("CISCO") no longer matches a holding unless written exactly as a name the holding is known by (`ted.winner_matches`). The concentration reader skips residual and regional buckets ("All Other", "Non-U.S."), sentences that say no customer reached the threshold, and sentences with several periods and several percentages (they go to the skipped list with a reason). More "if we lose / fail / are unable" wordings are screened as hypothetical. Not re-measured on the gate sample yet.
- **Defects 1-4.** `price_bar` needed no change (production already matches `20261005120000`); `hs_concordance` gets the two production columns; `disclosure_adjudication_history` keeps every overwritten label (trigger + append-only guard); `public.purge_source_documents(regulator)` backs out one source in order (tested on ESEF); `source_document_version.parser_version` may now be set after the fact; search paths fixed and `rls_auto_enable()` revoked from API roles. Defect 5 (migration-history timestamps) still needs the owner to run the reconcile script.
- **Hybrid accuracy check.** `adjudication_assist.py`, `POST /disclosures/adjudication/{dataset}/prelabel` and `/accept-suggestions`, and buttons on the accuracy page. The configured chat model suggests a label per item; suggestions are stored apart and never reach the gate. A person must label an item themselves when it is planted, the model is unsure, the model disagrees with the extractor, or it falls in a fixed 20% random share; the rest can be saved in one step as the person's own labels (note "accepted the model suggestion"). Needs a gpt-/gemini- model with a key.
- **ESEF values read out** (`esef_facts.py`, 11 IFRS concepts, dimensional facts kept apart, conflicting duplicates skipped, `facts` step on the esef job). EDINET values still need the keyed API.
- **Deal party matching** (`deal_parties.py`, `GET /disclosures/deals/parties/pending`, confirm and reject routes): a name or alias match is a suggestion until the owner confirms; rejections are remembered. No page for it yet.
- **Manifest.** 59 more model-checked slots (13F amendments, 13G, 8-K deal exhibits, XBRL notes, ESEF notes, relationship rows); `captured` stays 0. Eight disagreements between the app and the originals are listed in the thresholds doc (no deal rows from Item 1.01 8-Ks; amendment totals null; INTU period; multi-person 13G stored as one row; prior-year relationship values; ESEF text-block concept names; identical GB/SE ESEF bytes; passage whitespace).
- **Not done:** insider footnote and derivative-term backfill, structured 13G group members, valuation/screener/ROIC consumers of the valuation gate, alerts into thesis/monitor/news, a deal-party review page, the page restyle.

**B. Written but never executed here**

| Gap | Tasks | Close it by |
|---|---|---|
| Database test files skip without `STEWARD_TEST_DATABASE_URL`: `test_disclosure_db.py`, `_statements_db`, `_ownership_db`, `_insiders_db`, `_gap_closure_db`, `_nport_lane_db`, `_deals_db`, `_estimates_db` | P1.1 to P7 | Run them in CI against a disposable Postgres |
| The `deal_state` alert scan (`alerts.scan_deals`) and its migration have no test of their own; the alert-kind constraint name `disclosure_alert_kind_check` is the Postgres default and was not confirmed | P6.7 | Add a DB test: a documented transition raises one alert, a rumour none, a rerun none; confirm the constraint name on a real database |
| `pyright` was not run on any P6 or P7 module | P6, P7 | Run it in `apps/backend` |
| eslint warning: unused `versionSummary` in `components/disclosure/deals.tsx` | P6 frontend | Remove it or use it |
| Also skipping without a database: `test_disclosure_analytics_db.py`. None of the SQL in the P8 modules (`coverage_report`, `look_through`, `screening`, `replay`, the four new alert scans) has run | P8 | Run them in CI against a disposable Postgres |
| Assumed names never checked against a database: `quality_review_subject_type_check`, debt-maturity bucket keys `year_N` and `thereafter`, replay deal summary keys `id`/`title`/`state`, metric names `operating_cash_flow`/`net_income`/`cash`/`debt_due_year_1` against `formulas.py`, `disclosure_coverage.dataset` values against the coverage keys, N-PORT `pct_value` in percent | P8 | Run each query on real data and fix what differs |
| The new alert scans for controlling stake, insider buy cluster, estimate revision and debt maturity have no database test; throttling (5 per ticker, kind and day) is untested end to end | P8.5 | Add DB tests: one alert per event, a rerun raises none, a collection failure raises none |
| Passing as of this update: `tsc --noEmit`, `lint:colors`, ruff on `steward/disclosure`, `api` and the new tests, 346 offline backend tests (`-k "disclosure and not db"`) and 122 web tests in `lib/disclosure`. Not run: web tests outside `lib/disclosure`, `pyright`, any DB test | P6 to P9 | Re-run the full web and backend suites before merging |

**C. Buildable but not built**

| Area | Gap | Tasks |
|---|---|---|
| Scheduling | No scheduler runs ingest, identifier resolution, TR-1 collection, deal discovery or estimate collection; the routes are manual | P1.3, P2.2, P3.4, P6.2, P7.2 |
| Discovery | ESEF, EDINET and SEDAR+ are parsers for assumed response shapes; nothing stores or fetches with them. No 8-K/6-K/S-4/proxy discovery for deals. No collector connects the fetch governor to the consensus extractor | P1.3, P2.5, P2.6, P6.2, P7.2 |
| Identity | Corpus has no LEI/ISIN/FIGI until a person confirms matches; deal parties stay unmatched (`issuer_id` null) until reviewed; no reviewed-match gate on counterparties | P0.2, P1.5, P5.4, P6.5 |
| Ownership | Companies House PSC and GLEIF hierarchy not collected; national voting-rights lane `not_covered` everywhere until 20 reviewed UK examples; group members not structured | P3.2, P3.4, P3.5 |
| Insiders | Footnote and derivative-term backfill not run; no jurisdiction beyond UK PDMR; buyback and earnings-window context missing | P4.1, P4.2, P4.4, P4.5 |
| Dependencies | Passages not yet audited for existing suggestions; `claim_shape`, denominator and period coarse for old rows; two-sided reconciliation limited to identical relationships | P5.1, P5.3, P5.4 |
| Financials | ESEF not reconciled to 20-F; valuation, screener and ROIC consumers do not yet read the valuation gate; statement checks cover gross profit and free cash flow only | P2.5, P2.7, P2.8 |
| Deals | Dilution, debt/EV and segment-mix impacts are null in live mode (share counts, enterprise value and segment revenue not read from observations); extractor is English and US-format only; page filters by type and state only (size and currency are API-only) | P6.3, P6.6, P6.7 |
| Estimates | No pilot with two issuers' historical files; no UI to record the 20 pair reviews, so revision alerts and screens stay off; segment bridge shows the first period only; actuals come only from `disclosure_observation` | P7.1, P7.5, P7.7, P7.8 |
| Alerts | Alerts are stored and shown but not fed into the thesis, monitor or news flow; revision alerts stay off until the 20-pair gate passes | P4.6, P7.8, P8.5 |
| Coverage | The report exists (`/disclosures/data/coverage`) but its ownership count is 13D/G plus N-PORT only (no 13F), jurisdiction comes from the ISIN prefix then issuer country, and it covers held tickers only, not the watchlist | P9.7 |
| Product API | No `company/{id}/…` path family (reads stay under `/holdings/{ticker}/…`); date, source, verified-only and pagination filters exist only where earlier phases added them | P8.1 |
| Look-through and screen | Insider buys count only trades with plan status explicitly false; the sample-mode screen leaves matched holdings' values empty; the look-through is a panel, not its own page | P8.3, P8.4 |
| Research answer | The answer is templated from stored evidence with a numbers-appear-in-evidence guard; no model phrases it because the repo has no LLM helper | P8.6 |
| Replay | Replay covers the datasets the read modules expose with `as_of`; revised macro observations are not part of it; no UI test of the date picker | P8.7 |
| Point-in-time tests | Only consensus correction and replay separation are tested; document amendment, deal reversal, insider amendment, issuer rename and restatement through the SQL reads, and split/FX/share-class portfolio-math cases, are not written | P9.4, P9.5 |
| Adapter tests | No sweep of malformed XML/XBRL, HTML table shifts, PDF layout variation, rate limits, authentication denial and delayed publication per adapter | P9.1 |
| UI process | The three P8 pages were built without loading the frontend-design skill, copying existing components instead | P8 frontend |

**D. Structural risks to carry forward**

| Risk | What to do |
|---|---|
| Python and TypeScript hold copies of the same rules for every phase (sample mode, in-browser recompute) | Keep paired test suites in step; delete the TypeScript copies once live data exists everywhere |
| OpenAPI model names must be unique across route files: a second `SegmentBridgeOut` made the generator qualify both names and broke the existing financials types (fixed by renaming the estimates model to `EstimateSegmentBridgeOut`) | Prefix new response models with their dataset; check `schema.d.ts` after adding routes |
| A source with no free route is a coverage gap, not a licensing block (named analyst estimates, SEDAR+ bulk, EDINET) | Keep each as a stated gap on its page until an access test passes |

## Phase P8 — Product integration and cross-dataset analytics

**Dependency:** Published outputs from P2–P7. **Exit:** each dataset changes a decision or explicitly records why it cannot.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Integrate company evidence views into existing holding/research pages and portfolio surfaces: filing timeline, statements/notes, ownership lanes, insider activity, dependency network, deal lifecycle and estimate timeline. Provide a portfolio concentration network and screening controls with covered/uncovered universe counts. Every chart mark drills into its evidence and carries period, unit, known-at date, source, method, confidence and coverage denominator; provide accessible data tables and honest empty/stale/error states.


**Backend**
- [ ] `P8.1` Add owner-only read APIs under `apps/backend/src/api/` for `company/{id}/disclosures`, `/ownership`, `/insiders`, `/dependencies`, `/deals`, `/estimates`, `/financials?as_of=` and `/data/coverage`. Support date, period, source, verified-only and pagination filters; return provenance, access state and confidence without exposing API keys or unnecessary raw payloads. Update OpenAPI and generated web types.
- [ ] `P8.2` Define stable response models for as-filed financials/notes, separate control and institutional ownership, classified insider trades, dependency claims, deal timeline and estimates. Include unit, fiscal period, as-of/known-at, source, method and coverage state.
- [ ] `P8.3` Extend portfolio look-through and risk: aggregate direct and fund exposures to confirmed issuer relationships, major transaction impacts and concentrated customers; preserve unknown weights, mixed as-of dates and fund reporting lag. Calculate totals only over covered market value and show the denominator.
- [ ] `P8.4` Add screening and peer criteria based on verified fields: cash conversion, debt maturity, insider discretionary buys, stake change, customer concentration, segment surprise and deal exposure. Show universe size and excluded securities; avoid ranking missing data as a zero score.
- [ ] `P8.5` Feed confirmed changes into existing thesis/monitor/news flow: new controlling stake, material insider buy cluster, customer loss, deal state transition, debt maturity change and estimate revision. Deduplicate by evidence/event ID, throttle alerts and display why the rule fired. Suggested edges and unverified estimates generate review tasks, not high-confidence breach alerts.
- [ ] `P8.6` Add an evidence-aware research answer path: retrieve verified stored claims and their passage links; have the model explain them with dates and uncertainty. The model must not calculate authoritative totals or invent undisclosed customer identities/consensus.
- [ ] `P8.7` Add historical decision-replay API support: reconstruct holdings and only filings/estimates/deals known at the requested date, return later changes separately, and prevent future filings or revised macro observations from leaking into the historical result.


## Phase P9 — Verification, rollout and operations

**Dependency:** P0–P8. **Exit:** phased release with measured coverage, accuracy, technical access and zero paid cost.


**Frontend**
- [ ] Deliver this phase frontend in parallel with backend work, using fixture-backed typed contracts so UI implementation does not wait for live endpoints.
- Visually verify dense and sparse issuers, amended/restated histories, mixed jurisdictions, unknown denominators and provider failure. Reconcile every chart total to the backend/API and cited filing; check as-of replay excludes later filings and revisions. Release charts only after domain review confirms their encodings do not conflate control, holdings, reported estimates or inferred relationships.


**Backend**
- [ ] `P9.1` Add offline parser and migration tests for every enabled adapter; include malformed XML/XBRL, HTML table shifts, PDF layout variation, amendments, rate limits, authentication denial, duplicate documents and delayed publication. CI uses fixtures and no paid or private API keys.
- [ ] `P9.2` Run a 30-day shadow collection for held/watchlist issuers and the P0 corpus. Compare new outputs with current SEC/13F/insider/graph cards, measure incremental decision-critical coverage, request volume, parsing failures, entity match precision and technical access gaps. Keep low-value sources dormant.
- [ ] `P9.3` Manually adjudicate samples for each published dataset: 100 relationship claims; 50 owner/stake matches; 50 insider economic classifications; 30 deal states/terms; 20 consensus revision pairs; and 30 financial note values. Set and meet domain-specific precision targets from P0; report both numerator and denominator.
- [ ] `P9.4` Test point-in-time reconstruction around a filing amendment, consensus correction, deal reversal, insider amendment, issuer rename and financial restatement. Assert no output predates its source `known_at`, and recomputation after a parser change preserves the original observation lineage.
- [ ] `P9.5` Check portfolio math with split/FX/currency/share-class cases and disclosed versus unknown exposure. Test failure of each optional source: retain last verified fact with stale label, show unavailable where needed, and avoid alerting on collection failure.
- [ ] `P9.6` Validate backup/restore, retention and deletion by source, reviewer audit, RLS, secret handling, provider shutdown and migration rollback. Keep SEDAR+ and broker-estimate adapters active as pilots under the owner's permission; classify only authentication, retrieval, paid-only and data-coverage failures as gaps.
- [ ] `P9.7` Publish a coverage and quality report by dataset, jurisdiction, issuer and time period, including known blind spots, free-tier cost ledger ($0), sampled error rates and first/last known dates. Release screens in order: verified SEC/PIT foundation → ownership/insiders → relationships/deals → accessible estimates → additional jurisdictions.


### Implementation status and open gaps after P8–P9

Built (offline-tested only; nothing here has run against Postgres or been viewed in a browser):
- P8.1/P8.2 partial: `GET /disclosures/data/coverage`, `/company/{ticker}/evidence`. The per-dataset reads already existed under `/holdings/{ticker}/…`; a unified `company/{id}/…` path family was not added. Pagination and verified-only filters exist only where earlier phases added them.
- P8.3 `GET /disclosures/portfolio/look-through`; P8.4 `GET /disclosures/screen` (seven criteria, universe size, excluded with reason). The look-through is a panel on the screen page.
- P8.5 alert kinds `controlling_stake`, `insider_buy_cluster`, `estimate_revision`, `debt_maturity_change`, each with `why_fired`, throttled at 5 per ticker/kind/day; suggested edges and non-comparable or ungated estimates go to review tasks. Estimate-revision alerts stay off until the 20-pair review gate passes. Migration `20261007190000`.
- P8.6 `POST /disclosures/answer` is a templated answer over stored evidence with a numbers-must-appear-in-evidence guard. No LLM phrases it, because the repo has no model helper; the "model explains" half of P8.6 is not built.
- P8.7 `GET /disclosures/replay?as_of=` returns what was known and later changes separately.
- P9.4/P9.5 offline tests in `test_disclosure_pit.py` (consensus correction, replay separation, failure handling). P9.7 report fields inside the coverage route; pages `/disclosures/screen`, `/coverage`, `/replay`.

Open gaps:
- **Not built (needs real data or time):** P9.2 30-day shadow run, P9.3 manual adjudication samples, P9.6 backup/restore/retention/rollback drills and RLS audit, the P9.1 per-adapter malformed-input sweep, and the staged release order (a list is published in the coverage report, but nothing enforces it).
- **Not written:** DB-bound P9.4 cases (document amendment, deal reversal, insider amendment, issuer rename, restatement through the SQL reads) and split/FX/share-class portfolio-math tests.
- **Assumptions never checked against a database:** `quality_review_subject_type_check` and `disclosure_alert_kind_check` use Postgres default names; debt-maturity buckets are `year_N` and `thereafter`; the replay deals summary guesses `id`, `title`, `state`; metric names `operating_cash_flow`, `net_income`, `cash`, `debt_due_year_1` are assumed to match `formulas.py`; `disclosure_coverage.dataset` values are assumed to match the coverage keys.
- **Known scope limits:** the ownership count is 13D/G plus N-PORT, not 13F; jurisdiction comes from the ISIN prefix then issuer country; the screen universe is held tickers only, not the watchlist; insider buys count only trades with plan status explicitly false; N-PORT `pct_value` is assumed to be in percent; the sample-mode screen leaves matched holdings' values empty.
- **UI:** the three new pages follow existing components; the frontend-design skill was not loaded for them, and none was viewed in a browser.
- **Not wired:** P8.5 alerts do not yet feed the thesis/monitor/news flow beyond the alerts list, and P8.1's stale/known-at filters are not on every route.

## Dependencies on the two companion plans

- The [free data sources plan](free_data_sources_implementation_plan.md) provides D0 free-access controls, D1 identity/provenance, D4 financial/estimate taxonomy, D5 company graph, D6 funds/ownership and D7 product views. The P tasks are the deeper public-disclosure implementation for those rows, not a parallel vendor ingestion project.
- The [Morningstar-style features plan](morningstar_features_implementation_plan.md) may consume P2 for historical financial ratios and valuation, P3/P4 for stewardship/ownership context, P5 for moat and concentration, P6 for event-aware valuation, P7 for earnings/segment expectations, and P8 for X-Ray, screeners and portfolio alerts. Each consumer must carry the upstream coverage and known-at limits.

## Source references for formats and access methods

- [SEC EDGAR data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [SEC access and fair-use guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), [Financial Statement and Notes datasets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets), [N-PORT datasets](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets), [insider transaction datasets](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets), and [Forms 3/4/5 code guide](https://www.sec.gov/about/forms/forms-3-4-5.pdf).
- [ESMA ESEF reporting requirements](https://www.esma.europa.eu/issuer-disclosure/electronic-reporting), [FCA National Storage Mechanism](https://www.fca.org.uk/markets/primary-markets/regulatory-disclosures/national-storage-mechanism), [Companies House data products](https://www.gov.uk/guidance/companies-house-data-products), and [Japan EDINET API information](https://disclosure2.edinet-fsa.go.jp/week0020.aspx).
- [TED Search API](https://docs.ted.europa.eu/api/latest/search.html), [GLEIF Level 2 meaning](https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-2-data-who-owns-whom), and [SEDAR+ portal](https://sedarplus.ca/).
- Examples of issuer-posted consensus are [Shell](https://www.shell.com/investors/results-and-reporting/consensus-estimates.html) and [LSEG](https://www.lseg.com/en/investor-relations/consensus); test each page's retrieval, versioning and metric definitions.
