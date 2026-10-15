# Macro, trade, input costs, currency and customer / supplier dependencies — plan

## Status (2026-10-06)

Built: geography exposures from XBRL (`exposures/build.py`); currency and commodity inputs from the
annual report (`exposures/inputs.py`), with each row carrying origin, quote, period and filing URL;
product lines to HS headings, always `estimated` (`exposures/hs.py`); customer and supplier
disclosures from XBRL concentration facts and from filing text with a second AI approver
(`graph/dependencies.py`); counterparty resolution to holdings and mirror-relationship
corroboration; archived results documents as the source for issuers without an EDGAR report
(`exposures/archived.py`); FRED commodity series in the macro defaults and series linking on
`/holdings/{ticker}/exposure-context` (`macro/exposure_series.py`); `POST
/holdings/{ticker}/exposures/remove` and the holding panels show who decided, the citation and a
one-click overturn.

Differences from the plan: the dependency extractor is a step of `exposure_build` (one schedule,
one settings block) instead of a separate `dependency_extract` job, and the approver lives in
`graph/dependencies.py` instead of `approvals/ai.py`; an XBRL share benchmarked to receivables is
stored with basis `other`, not as a revenue share; the OECD feeds stay closed (see
`docs/data/provider-access.md`).

Not yet run against live data: the AI steps, the archived-document path (CSG, Chugai, Daiichi) and
the commodity collection (needs `macro_collect` and `FRED_API_KEY`).

Goal: fill the holding-page sections "macro", "trade", "input costs and currency", "country
and industry context" and "dependencies" without the owner typing anything, from reported
company data first, with AI extraction and AI approval second, and every figure traceable to
a citation. The owner can overturn any entry.

## Why they are empty today

All of these sections are driven by **exposure and disclosure rows that nothing creates
automatically**:

| Section | Reads | Today |
|---|---|---|
| Macro / trade / country and industry context | `macro_exposure` rows (`issuer_id`, `segment`, `geography`, `hs_code`, `weight`, `basis` = reported / estimated / user_assigned, `valid_from`) via `/macro/exposures` and `/research-context/*` | 0 rows. The macro and trade collectors fetch series, but with no exposure rows nothing links a series to a holding |
| Input costs and currency | the holding profile's `exposures` list (`revenue_currency`, `revenue_region`, `commodity_input`, `other`, each with a `share`) via `/holdings/{ticker}/exposure-context` | empty for every holding |
| Dependencies (customers and suppliers) | `customer_supplier_disclosure` rows (relationship, direction, verbatim passage, source URL, reported share, status) via `/customer-supplier-concentration` | none; only owner-confirmed, dated rows with a known direction are counted |

The data to build them is mostly already in the database or is free and public.

## Sources, in order (structured first, web last)

1. **Reported structured data (already collected).** SEC XBRL facts for revenue by geography
   and by segment (`/holdings/{ticker}/segments` already returns 79–216 values for the US
   stocks), and XBRL concentration tags (customer and supplier concentration percentages with
   the customer named as an axis member). ESEF and EDINET equivalents for EU / Japanese
   issuers where they exist.
2. **Filing text (AI extraction with quote verification).** The latest 10-K / 20-F / annual
   report sections: Item 1 (customers, suppliers, raw materials), Item 1A (concentration
   risks), Item 7A (currency and commodity risk), the geographic-information note. Each
   extracted figure carries a verbatim quote that the existing claim verifier checks against
   the document text before it is stored (the same rule as earnings figures).
3. **Company IR documents** for issuers without SEC filings (CSG, Chugai, Daiichi), through the
   discovery and gap-research jobs in `investor_page_discovery_plan.md`.
4. **Web search**, last resort and only from the reliable-source tiers defined there.

## Design

### A. Exposure builder (`exposures/build.py`, job `exposure_build`, weekly + after each new
annual filing)

For each holding:

1. **Geography.** Revenue by region / country from XBRL `segment`/geography facts -> one
   `macro_exposure` row per region with `basis = reported`, `weight = region revenue / total`,
   `valid_from` = the fiscal year end. Region labels are mapped to the ISO-style geography
   codes the macro collectors use (`macro/geo.py`); a label that cannot be mapped is kept as
   an "other / unmapped" row so weights still sum to <= 1 (the per-dimension 100% cap is
   enforced by the database check).
2. **Currency.** Revenue currency shares from the same geography table where the filing
   reports them, otherwise from the AI-extracted foreign-currency risk passage. Written to
   the profile-level exposure list as `revenue_currency` with `share` and a citation. A
   holding whose reporting currency differs from the portfolio base currency is also flagged
   (this already exists for FX conversion; here it only supplies the exposure).
3. **Input costs.** Commodity and material inputs named in Item 1 / 7A (copper, aluminium,
   oil, wafers, helium...), each as `commodity_input` with a `share` only when the filing
   gives one; otherwise the row exists without a share (name only), which is enough to link
   price series and is honestly shown as "share not disclosed".
4. **Trade.** HS product codes: an AI mapping from each material or product line to an HS
   code is only ever `basis = estimated`, always flagged, and used only to attach the trade
   series; it is never mixed into reported weights (the existing dimension rule already keeps
   reported and estimated apart).

Reported weights come from numbers in a filing. Anything inferred is `estimated`. Nothing is
`user_assigned` unless the owner enters it, and an owner row is never overwritten.

### B. Series linking (the macro / trade / input-cost views)

- Currency exposure -> the FX series the app already stores (CNB / ECB), as a rolling
  contribution to the holding's currency risk.
- Commodity exposure -> a price series per commodity from the free sources that are already
  enabled (FRED / World Bank commodity series, Yahoo futures symbols). **To verify before
  building:** which commodity series are actually collected today; missing ones are added to
  the macro series config, not hard-coded.
- Region exposure -> country macro indicators and trade flows from the collectors
  (`macro/collect.py`, `public_collect.py`), including the OECD feeds whose access gate is
  currently closed ("access gate closed (see Data sources)"): decide whether to open them or
  rely on Eurostat / World Bank, and say so on the page.
- Output per holding: for each exposure, the series, its latest change, and the holding's
  weighted effect (`macro/exposure.py::weighted_effect`), with the mapped / unknown split the
  page already shows.

### C. Dependency extractor (`graph/dependencies.py`, job `dependency_extract`)

1. **XBRL concentration tags first**: customer / supplier concentration percentages with a
   named customer member -> a disclosure row with `reported_share`, period, and the XBRL fact
   as evidence.
2. **Filing text**: AI extracts statements such as "Customer A accounted for 22% of revenue",
   "we depend on TSMC to manufacture..." into disclosure rows: relationship (customer /
   supplier), direction (who depends on whom), counterparty name or key, anonymous flag,
   `reported_share` (+ basis: share of the issuer's revenue or purchases, never of the
   counterparty's), period, and the **verbatim passage and source URL**. The passage must be
   found in the document text or the row is rejected.
3. **Counterparty resolution**: names are matched to holdings and to `issuer` records (GLEIF
   / SEC) so a counterparty that is itself a holding links both ways (e.g. a supplier that is
   in the portfolio).
4. **Two-sided checks**: if A says "B is 15% of revenue" and B's own filing says it depends on
   A, both rows corroborate each other (higher confidence, used by the approver).

### D. AI approval (same policy as listings and peers)

Rows are created as `suggested`; the AI approver (see `approvals/ai.py`) confirms when:

- the quote is verified in the source text, the figure and unit match, the direction is
  unambiguous, the counterparty is resolved or explicitly anonymous, and confidence >= the
  configured threshold; anything else stays suggested (visible, not counted);
- reported rows (XBRL facts, verified quotes) are auto-confirmed; estimated rows (HS mapping,
  inferred cost shares) are confirmed only as `estimated` and always labelled.

Each decision stores model, confidence, reasons and the policy version. The owner can reject
or edit any row; an owner decision is final and is never re-suggested or overwritten.

### E. Non-SEC issuers

CSG, Chugai and Daiichi have no XBRL facts in the store. Their exposures come from the same
extractor run on their results / annual-report PDFs (stored through the earnings pipeline's
document archive), which requires the IR pages from the discovery plan. The H1 2026 CSG
results statement is already archived (job 349), so CSG is the first test case.

## Changes by area

Backend
- `exposures/build.py`, `graph/dependencies.py` (new) and their prompts (registered in
  `config/prompts.py` so they can be overridden like the others).
- Jobs `exposure_build`, `dependency_extract`: `jobs/registry.py`, `config/jobs.py`
  (schedule + settings: model, effort, max documents per run, confidence threshold),
  `config/budget.py`, `worker/catchup.py`.
- Extend `approvals/ai.py` with exposure and disclosure queues.
- Migrations only if a provenance column is missing (evidence id, model, confidence on
  `macro_exposure`); the disclosure table already has status, passage, source and evidence.
- `docs/data/provider-access.md`.

Web
- `lib/config/jobs-form.ts` (+ test), regenerated `openapi.json` / `schema.d.ts`.
- Exposure and dependency panels show the basis (reported / estimated), the citation and who
  decided (AI or owner) with a one-click overturn.

## Tests

- XBRL geography facts -> weights sum <= 1, region labels mapped, unmapped kept as "other".
- Quote not in the document -> row rejected; share of the counterparty's revenue -> rejected.
- Estimated HS mapping never counted in reported weights.
- Counterparty that is a holding links both ways; anonymous counterparty carries no name.
- Owner-edited or owner-rejected row never overwritten or re-suggested.
- Registry / catch-up / jobs-form consistency tests.

## Cost and limits

Most work is deterministic (XBRL). The AI calls read a few sections of one filing per holding
per year (about 30 holdings x a few thousand tokens), plus the approval calls: well inside the
monthly cap. Runs are triggered by a new annual filing, not on a timer. Facts that cannot be
sourced stay empty with the reason shown, never guessed.

## Rollout

1. Build the exposure builder for geography only, test on AAPL, NVDA, META (rich XBRL).
2. Add currency and commodity extraction; check against the filings by hand for three holdings.
3. Add the dependency extractor (XBRL tags, then text).
4. Turn on AI approval for each queue, with the weekly summary of what it approved.
5. Run CSG, Chugai and Daiichi from their archived documents.
