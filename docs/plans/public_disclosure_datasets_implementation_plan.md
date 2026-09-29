# Public-disclosure datasets implementation plan

Research snapshot: 29 September 2026. Companions: [free data sources plan](free_data_sources_implementation_plan.md) and [Morningstar-style features plan](morningstar_features_implementation_plan.md).

## Goal, scope and hard boundaries

Build the additional datasets in the owner's two frameworks from **free, accessible sources**, then turn them into company, stock and portfolio decisions. The owner has confirmed permission for the intended personal use, so licensing is not a blocker in this plan. This plan covers institutional ownership; insider transactions; customer, supplier and revenue-dependency relationships; historical analyst consensus; individual analyst estimates where actually accessible; estimate revisions; point-in-time global fundamentals; public deal/transaction terms; and segment-level estimates. It also covers the shared filings, event, evidence and entity-resolution systems needed to make those datasets credible.

The product is the owner's personal, single-user, self-hosted app. Its data is for private research and portfolio decisions; sharing, resale and public distribution are outside this plan. Use the owner's permission as the authorization assumption. Test actual authentication, free access, retrieval stability and rate limits before scheduling each collector. Never pay, enter a trial that converts to paid, bypass technical access controls or manufacture a consensus from management guidance.

**Definition of done:** a dataset must have an evidence-backed schema, dated coverage and uncertainty, an owner-visible use in a screen or alert, an as-of query, offline fixtures, an access record, and measurable accuracy. Ingesting a document alone is not completion.

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

- [ ] `P0.1` Create `docs/data/public-disclosure-source-registry.md` with each specific form, API endpoint or issuer page, jurisdiction, registration/authentication method, cost, rate limit, document formats, publication lag, tested date, responsible owner and technical stop condition. Link to the access table in D0.1/D0.7 and record the owner's permission as the shared assumption.
- [ ] `P0.2` Assemble a reproducible 45-issuer corpus: at least 15 US, 10 ESEF Europe, 5 UK, 5 Japan, 5 Canada and 5 other jurisdictions; include an ADR, dual listing, fund, delisted issuer, renamed issuer, conglomerate and a company with a material disclosed customer. Use documents from distinct years and amendments. Map to existing held/watchlist identifiers without storing personal transactions in fixtures.
- [ ] `P0.3` Capture 10 samples each for 13F amendment, 13D/G group, Form 4 purchase/sale and non-discretionary activity, N-PORT, 8-K deal/exhibit, XBRL note, ESEF block note, SEDAR+ Canadian filing, issuer consensus version and relationship disclosure. Record exact expected parsed fields, published dates and manual ground truth.
- [ ] `P0.4` Test SEC fair-access behavior, conditional fetch, archive URL stability and bulk files; test FCA public download separately from its submission API, EDINET key registration, Companies House/TED read routes, issuer IR pages and SEDAR+ search/export. For SEDAR+, design a Canadian pilot around stable IDs, incremental discovery, rate controls and fallback issuer files. Record technical failures as integration gaps to solve, not licensing blocks.
- [ ] `P0.5` Inventory current SEC/ESEF/ownership/insider/news tables and API consumers. Draw a migration map that identifies which existing row is extended, which new record is shared evidence, and which old suggestion must remain unverified. Baseline current dataset coverage and known false-positive rate.
- [ ] `P0.6` Define release thresholds before coding: required evidence precision, identity match precision, numerical reconciliation, freshness and accessible-source coverage for each dataset; assign manual adjudication samples and an explicit zero-dollar cost check.

## Phase P1 — Filing corpus, identity and point-in-time foundation

**Dependency:** P0. **Exit:** a filing, a later amendment and a restatement can be queried at three historical cutoffs without future knowledge appearing.

- [ ] `P1.1` Add additive Supabase migrations for the shared schema above, with unique document/version keys, indexes for `(issuer_id, known_at)` and `(subject_id, object_id, valid_from)`, owner RLS and migration tests. Retain old IDs and foreign keys; use references/backfills, not a breaking rewrite of held-ticker tables.
- [ ] `P1.2` Implement the source registry and fetch governor under `sources/`: free-access and authentication check before requests, request budget, user agent, retry-after, incremental cursors, checksum deduplication, failure classification and observability. Archive fetched original documents and link them to their source URL/hash for point-in-time replay.
- [ ] `P1.3` Build document discovery for SEC submissions and targeted archive exhibits, then ESEF/issuer IR pilot. Parse filing index, primary document, exhibit list and XBRL package. Store accepted, filed, published, first-seen and retrieved dates separately; missing dates stay null with an explanation.
- [ ] `P1.4` Add parser outputs for document sections, HTML tables, XML/XBRL facts, footnotes and text blocks with stable evidence offsets. Store extraction model/rule version; changes create new claims without silently replacing earlier claims. Quarantine malformed or inaccessible documents.
- [ ] `P1.5` Extend `reference/identity.py` with legal entity, security, share class, listing, CIK, LEI, ISIN/FIGI, MIC, issuer aliases and dated successor rules. Require a reviewed mapping before joining a disclosure to a portfolio instrument; support anonymous counterparties without inventing a company ID.
- [ ] `P1.6` Implement `as_of` repository methods and a provenance read endpoint. Query one selected issuer at pre-filing, post-filing and post-amendment cutoffs; verify values, relationships and events match contemporaneous knowledge. Show source version and evidence span in the review UI.
- [ ] `P1.7` Add a reviewer queue for low-confidence parsing, entity ambiguity, contradictions and source-format changes. Decisions record the original candidate, reviewer and reason; a denied or superseded claim is auditable and excluded from authoritative calculations.

## Phase P2 — Point-in-time financial statements and notes

**Dependency:** P1; free data plan D4.1/D4.8/D4.9. **Exit:** reconciled US statements and a smaller ESEF pilot support historical valuation without restatement leakage.

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

- [ ] `P3.1` Extend 13F coverage from held CUSIPs to prioritized watchlist names/managers, including amendments, confidential-treatment indicators and manager identity changes. Reconcile totals, option put/call and voting/investment-discretion fields; do not call a 13F position a beneficial stake.
- [ ] `P3.2` Ingest Schedule 13D/G and amendments for the pilot issuers. Capture filer and group members, class, shares/percentage, reported voting/dispositive power, purpose of transaction, agreement/exhibit links and measurement/filing times. Link later amendments and compute a *reported* stake delta only for the same class and comparable basis.
- [ ] `P3.3` Pilot public N-PORT holdings for matched US funds, report date, filing date, securities and derivatives. Compare to issuer fund files; attribute differing dates, share classes, cash and derivatives. Never combine a fund holding with its manager's 13F as if they were two independent owners.
- [ ] `P3.4` Build national major-holder adapter contracts (UK TR-1 first, then BaFin/AMF/AFM/CONSOB, Japan EDINET, Canada SEDAR+ and ASX). For each, record disclosure threshold, voting rights versus economic interest, direct/indirect chain, denominator and publication lag. Enable one jurisdiction only after 20 reviewed examples meet P0 quality.
- [ ] `P3.5` Use Companies House PSC/GLEIF to corroborate legal-entity control and hierarchy, explicitly as separate control data. Keep a conflict review when listed-issuer beneficial ownership and corporate consolidation appear inconsistent.
- [ ] `P3.6` Add company ownership view: 13F manager trends, reported 13D/G control stakes, fund holdings, sources/as-of dates, top changes and an unknown remainder. Add watchlist alerts for a verified major-holder filing or material stake amendment; avoid interpreting quarter-to-quarter 13F movement as trading intent.
- [ ] `P3.7` Test 13F amendment replacement, same-manager name changes, CUSIP reuse, ADR/ordinary class, group aggregation, conflicting share denominators, N-PORT lag and non-US threshold differences. Sample manually before publishing any “top owners” or control alert.

## Phase P4 — Insider economic activity

**Dependency:** P1; existing Forms 3/4/5 pipeline. **Exit:** a reader can distinguish discretionary open-market activity from mechanical compensation and tax events.

- [ ] `P4.1` Backfill existing US insider rows with exact filing/evidence IDs, issuer/insider identifiers, officer/director/10% owner roles, transaction date versus filing date, direct/indirect ownership, security class, price, shares, post-transaction holdings and amendment lineage.
- [ ] `P4.2` Parse Form 4/5 footnotes and derivative table links, with referenced conversion/exercise price and expiry when disclosed. Retain original transaction code and reported 10b5-1 or non-10b5-1 plan indicator where the form provides it; never infer a plan from recurring dates alone.
- [ ] `P4.3` Implement a documented economic classifier: open-market `P` purchase, `S` sale, `M` option exercise, `A` grant/award, `F` tax withholding, `G` gift, and other/unknown. Treat linked exercise-and-sale bundles together; discretionary net buying excludes grants, gifts, taxes, transfers and unknowns. Let reviewers override a misclassified filing with reason.
- [ ] `P4.4` Pilot UK PDMR disclosures from FCA/issuer announcements; map actor, instrument, venue, nature, volume, price and notification date. Add further jurisdictions, including Canadian and Japanese sources, when retrieval, taxonomy and reviewer accuracy pass P0 thresholds.
- [ ] `P4.5` Build insider analysis: open-market net shares/value by role and 30/90/365-day window, clusters of independent insiders, ownership after trade, market-cap/float context and plan/derivative caveats. Link to dilution, buybacks and earnings/deal windows without assigning motive.
- [ ] `P4.6` Alert only on material verified open-market activity with deduplication by filing/amendment. Test corrections, split-adjusted shares, zero-price grants, derivative conversion, option exercise plus same-day sale and indirect trust holdings against reviewed examples.

## Phase P5 — Customer, supplier and revenue-dependency network

**Dependency:** P1/P2 and existing `news/regulatory.py` suggestions. **Exit:** every confirmed edge has a cited passage, direction, dates and named/anonymous status.

- [ ] `P5.1` Audit all existing SEC keyword-generated relationship suggestions. Fetch their exact passages and label true relationship, historical-only, hypothetical, negated, unrelated third party or ambiguous. Establish a precision baseline; do not auto-promote prior suggestions.
- [ ] `P5.2` Build structured-first extractors for 10-K/20-F/10-Q/6-K customer concentration, segment notes, material agreements and exhibits; then issuer releases/IR and procurement notices. Capture section and paragraph/table offsets, contract term, exclusivity, minimum purchase and termination if actually disclosed.
- [ ] `P5.3` Represent named buyer/supplier, anonymized “Customer A” or “three customers,” and industry/geography-only exposure as different claim shapes. Store reporter, counterparty, economic direction, product/service, fiscal period, revenue or purchases percentage/amount and denominator. Do not assign an anonymous label to a guessed real entity.
- [ ] `P5.4` Resolve named entities through CIK/LEI/registered name, dated aliases and subsidiaries. Require a reviewed entity match where two brands or affiliates overlap; document whether the edge is at issuer, subsidiary or business-unit level. Reconcile disclosures from both sides when possible and retain contradictions.
- [ ] `P5.5` Add TED award adapter for targeted issuer/legal-entity aliases. Parse buyer/supplier, award or lot ID, CPV, award value/currency, start/end and status. Deduplicate corrections and multiple lots; classify procurement as a contract signal, not a reported issuer revenue share.
- [ ] `P5.6` Maintain time-bounded claims and a confirmed graph edge only after rule or reviewer validation. Measure relationship precision on at least 100 sampled claims including deliberate false-positive phrases. Surface source age and unverified status in all graph reads.
- [ ] `P5.7` Calculate customer concentration, largest disclosed dependency and portfolio network exposure using only reported percentages or clearly labeled owner-assigned weights. Keep an unknown remainder. Run a shock scenario through one or two confirmed hops, with dates and lower/upper coverage; avoid numerical propagation from qualitative-only edges.
- [ ] `P5.8` Add company network and portfolio concentration views, change alerts for new/ended material relationships, and a review affordance for suggested edges. A user can trace each edge and percentage to the exact filing paragraph or table.

## Phase P6 — Public deal, financing and corporate-action terms

**Dependency:** P1/P2/P5. **Exit:** material public transactions have an evidence-linked lifecycle and quantified portfolio implications where terms permit.

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

- [ ] `P7.1` Create issuer IR consensus registry: URL/discovery method, publisher, whether values originate from third-party brokers, accessible format, authentication, update cadence and sample files. Use at least two issuers with reliably retrievable historical versions for the pilot; record missing files or incompatible definitions as coverage gaps, not licensing blocks.
- [ ] `P7.2` Fetch accessible HTML/PDF/XLSX files with version hashes and publication timestamps. Extract headings, footnotes, panel size, fiscal-year/quarter definitions, currency, reported versus adjusted basis, mean/median/high/low and metric/segment taxonomy. Archive source versions for historical replay.
- [ ] `P7.3` Keep `company_guidance`, `published_analyst_consensus`, `named_individual_estimate`, `owner_scenario` and `event_calendar_estimate` separate. Record if the publisher supplies consensus only for selected metrics or brokers. A management target never becomes an analyst estimate; a vendor event estimate is not a full historical consensus panel.
- [ ] `P7.4` Implement revisions only between comparable snapshots: same issuer, metric, scope/segment, period, unit, currency, accounting basis and comparable panel methodology. Calculate absolute/percentage revision, elapsed days, analyst-count change if available and direction; flag method/panel breaks rather than a numerical “revision.” Do not interpolate missing days.
- [ ] `P7.5` Where an issuer publishes segment consensus, map each segment definition to reported segments for that fiscal period. Preserve recasts and non-additive/elimination lines. Compute actual-versus-consensus by segment with a cited bridge; missing segment estimates stay missing.
- [ ] `P7.6` Capture individual broker estimates from any free accessible source that names the analyst/broker, metric, period and publication time, including an owner-provided source if available. Preserve version history and source identity. If no source returns the required data, show a technical/coverage gap; never infer analyst-level values from a consensus mean/range.
- [ ] `P7.7` Build company estimate timeline and event comparison: prior/latest accessible snapshot, revision markers, reported actual, surprise formula, panel-size/basis warnings and linked IR file. Use estimate snapshots known before earnings release; a later archived file cannot be used to score the earlier surprise.
- [ ] `P7.8` Test fiscal-calendar roll, company restatement, changed adjusted definition, currency restatement, panel replacement, segment recast, missing analyst count and corrected IR file. Review at least 20 snapshot pairs before enabling revision alerts or factor-like screens.

## Phase P8 — Product integration and cross-dataset analytics

**Dependency:** Published outputs from P2–P7. **Exit:** each dataset changes a decision or explicitly records why it cannot.

- [ ] `P8.1` Add owner-only read APIs under `apps/backend/src/api/` for `company/{id}/disclosures`, `/ownership`, `/insiders`, `/dependencies`, `/deals`, `/estimates`, `/financials?as_of=` and `/data/coverage`. Support date, period, source, verified-only and pagination filters; return provenance, access state and confidence without exposing API keys or unnecessary raw payloads. Update OpenAPI and generated web types.
- [ ] `P8.2` Extend existing holding/research pages with tabs or sections, rather than parallel profiles: as-filed financials/notes, control and institutional ownership, classified insider trades, dependency graph, deal timeline and estimates. Each numeric card has unit, fiscal period, as-of/known-at, source, method and coverage state.
- [ ] `P8.3` Extend portfolio look-through and risk: aggregate direct and fund exposures to confirmed issuer relationships, major transaction impacts and concentrated customers; preserve unknown weights, mixed as-of dates and fund reporting lag. Calculate totals only over covered market value and show the denominator.
- [ ] `P8.4` Add screening and peer criteria based on verified fields: cash conversion, debt maturity, insider discretionary buys, stake change, customer concentration, segment surprise and deal exposure. Show universe size and excluded securities; avoid ranking missing data as a zero score.
- [ ] `P8.5` Feed confirmed changes into existing thesis/monitor/news flow: new controlling stake, material insider buy cluster, customer loss, deal state transition, debt maturity change and estimate revision. Deduplicate by evidence/event ID, throttle alerts and display why the rule fired. Suggested edges and unverified estimates generate review tasks, not high-confidence breach alerts.
- [ ] `P8.6` Add an evidence-aware research answer path: retrieve verified stored claims and their passage links; have the model explain them with dates and uncertainty. The model must not calculate authoritative totals or invent undisclosed customer identities/consensus.
- [ ] `P8.7` Add historical decision replay: select a past date, reconstruct holdings and only the filings/estimates/deals known then, show what later changed, and prevent future filings or revised macro observations from leaking into the old analysis.

## Phase P9 — Verification, rollout and operations

**Dependency:** P0–P8. **Exit:** phased release with measured coverage, accuracy, technical access and zero paid cost.

- [ ] `P9.1` Add offline parser and migration tests for every enabled adapter; include malformed XML/XBRL, HTML table shifts, PDF layout variation, amendments, rate limits, authentication denial, duplicate documents and delayed publication. CI uses fixtures and no paid or private API keys.
- [ ] `P9.2` Run a 30-day shadow collection for held/watchlist issuers and the P0 corpus. Compare new outputs with current SEC/13F/insider/graph cards, measure incremental decision-critical coverage, request volume, parsing failures, entity match precision and technical access gaps. Keep low-value sources dormant.
- [ ] `P9.3` Manually adjudicate samples for each published dataset: 100 relationship claims; 50 owner/stake matches; 50 insider economic classifications; 30 deal states/terms; 20 consensus revision pairs; and 30 financial note values. Set and meet domain-specific precision targets from P0; report both numerator and denominator.
- [ ] `P9.4` Test point-in-time reconstruction around a filing amendment, consensus correction, deal reversal, insider amendment, issuer rename and financial restatement. Assert no output predates its source `known_at`, and recomputation after a parser change preserves the original observation lineage.
- [ ] `P9.5` Check portfolio math with split/FX/currency/share-class cases and disclosed versus unknown exposure. Test failure of each optional source: retain last verified fact with stale label, show unavailable where needed, and avoid alerting on collection failure.
- [ ] `P9.6` Validate backup/restore, retention and deletion by source, reviewer audit, RLS, secret handling, provider shutdown and migration rollback. Keep SEDAR+ and broker-estimate adapters active as pilots under the owner's permission; classify only authentication, retrieval, paid-only and data-coverage failures as gaps.
- [ ] `P9.7` Publish a coverage and quality report by dataset, jurisdiction, issuer and time period, including known blind spots, free-tier cost ledger ($0), sampled error rates and first/last known dates. Release screens in order: verified SEC/PIT foundation → ownership/insiders → relationships/deals → accessible estimates → additional jurisdictions.

## Dependencies on the two companion plans

- The [free data sources plan](free_data_sources_implementation_plan.md) provides D0 free-access controls, D1 identity/provenance, D4 financial/estimate taxonomy, D5 company graph, D6 funds/ownership and D7 product views. The P tasks are the deeper public-disclosure implementation for those rows, not a parallel vendor ingestion project.
- The [Morningstar-style features plan](morningstar_features_implementation_plan.md) may consume P2 for historical financial ratios and valuation, P3/P4 for stewardship/ownership context, P5 for moat and concentration, P6 for event-aware valuation, P7 for earnings/segment expectations, and P8 for X-Ray, screeners and portfolio alerts. Each consumer must carry the upstream coverage and known-at limits.

## Source references for formats and access methods

- [SEC EDGAR data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [SEC access and fair-use guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), [Financial Statement and Notes datasets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets), [N-PORT datasets](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets), [insider transaction datasets](https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets), and [Forms 3/4/5 code guide](https://www.sec.gov/about/forms/forms-3-4-5.pdf).
- [ESMA ESEF reporting requirements](https://www.esma.europa.eu/issuer-disclosure/electronic-reporting), [FCA National Storage Mechanism](https://www.fca.org.uk/markets/primary-markets/regulatory-disclosures/national-storage-mechanism), [Companies House data products](https://www.gov.uk/guidance/companies-house-data-products), and [Japan EDINET API information](https://disclosure2.edinet-fsa.go.jp/week0020.aspx).
- [TED Search API](https://docs.ted.europa.eu/api/latest/search.html), [GLEIF Level 2 meaning](https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-2-data-who-owns-whom), and [SEDAR+ portal](https://sedarplus.ca/).
- Examples of issuer-posted consensus are [Shell](https://www.shell.com/investors/results-and-reporting/consensus-estimates.html) and [LSEG](https://www.lseg.com/en/investor-relations/consensus); test each page's retrieval, versioning and metric definitions.
