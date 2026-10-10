# Public-disclosure source registry

Written **2026-10-07** for plan P0.1 / P0.4 ([public disclosure datasets plan](../plans/public_disclosure_datasets_implementation_plan.md)). Companion to [provider-access.md](provider-access.md), which covers the paid-tier-gated market-data providers; this file covers regulators, registries and issuer pages.

**Shared assumption.** The owner has confirmed permission for this personal, single-user, self-hosted use. That is a project assumption, not an access result. A source is enabled only after a probe recorded `available` in `disclosure_source` (the database refuses `enabled = true` otherwise). No payment, trial or technical-control bypass is ever used.

**How to refresh the "Observed" column.** `cd apps/backend && uv run python -m steward.disclosure.probe` makes about 20 polite requests (declared User-Agent, 0.5 s apart), prints the table below and writes nothing. Record a verdict with `steward.disclosure.registry.record_access_test`. The specs themselves live in code (`steward/disclosure/registry.py`) and are seeded by `seed_sources`.

## Observed access, 2026-10-07

Probed from the owner's workstation with the credentials in `.env` (Companies House key present; no EDINET key; `SEC_USER_AGENT` present).

| Source key | Endpoint probed | Observed | Verdict | What it does and does not prove |
|---|---|---|---|---|
| `sec.submissions` | `data.sec.gov/submissions/CIK0000320193.json` | HTTP 200, 164 KB, 1,000 recent filings, `acceptanceDateTime` present | available | Acceptance time exists, so `known_at` can be exact. **No `ETag`/`Last-Modified` header was returned**, so conditional fetch is unproven for this endpoint; incremental discovery must use the stored cursor (`known_at`, accession), not HTTP validators. |
| `sec.archive` | `www.sec.gov/Archives/edgar/data/320193/000032019324000123/index.json` | HTTP 200, 10 KB | available | One accession folder is readable as JSON. Archive URL stability over time is not yet measured. |
| `sec.companyfacts` | `data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json` | HTTP 200, 3.8 MB | available | Large per issuer: fetch for pilot issuers only. |
| `sec.fsn_bulk` | `.../financial-statement-and-notes-data-set/2025q2_notes.zip` | HTTP 404 | **not verified** | The URL pattern in the probe is wrong or the set is named differently. Not a coverage verdict: find the real file name on the SEC data-sets page and re-probe. |
| `sec.nport_bulk` | `.../form-n-port-data-sets/2025q3_nport.zip` | HTTP 206 on a ranged GET, `Accept-Ranges: bytes` | available | Ranged reads work, so a header/size check does not need the whole archive. |
| `esef.filings_xbrl_org` | `filings.xbrl.org/api/filings?page[size]=1` | HTTP 200, 1.2 KB | available | The JSON index answers. Package download and per-issuer LEI lookup are not yet probed. |
| `fca.nsm` | `api.data.fca.org.uk/search?index=fca-nsm-searchdata` (POST) | HTTP 400 | retrieval_failed | **The request body in the probe is a guess**; 400 means "bad request", not "no public route". Capture the browser's real search request from the NSM site and re-probe. This is an integration gap, not a licensing block. |
| `companies_house.api` | `/company/00000006` (Basic auth with the free key) | HTTP 200, `X-Ratelimit-Remain: 598` | available | Free key works; the published ceiling is about 600 requests per 5 minutes. PSC and officer endpoints not yet probed. |
| `edinet.api` | `api.edinet-fsa.go.jp/api/v2/documents.json` | not run: no `EDINET_API_KEY` | untested | Register for a free key (see below), set `EDINET_API_KEY`, re-run. |
| `ted.search` | `api.ted.europa.eu/v3/notices/search` (anonymous POST) | HTTP 200, 6 KB | available | Anonymous search works with a country query. Name-based buyer/supplier targeting not yet probed. |
| `gleif.level2` | `api.gleif.org/api/v1/lei-records/<LEI>/direct-parent-relationship` | HTTP 404 for Apple | available | For GLEIF a 404 here is a valid Level 2 answer ("no reported parent"). Need an entity that does report a parent to confirm the positive shape. |
| `sedarplus.portal` | `www.sedarplus.ca/` | HTTP 200, portal HTML | **untested** | Reachability only. Search/export, stable document IDs, incremental polling and any registration flow need a browser-session test and are **not** proven. Fall back to issuer-hosted filings for Canadian pilots until they are. |
| `issuer_ir.consensus` (Shell) | `shell.com/.../consensus-estimates.html` | HTTP 200, no xls/pdf links in the served HTML | not_covered (as served) | The file link may be injected by script, or Shell publishes a table in the page. Needs a page-level look before it counts as a versioned source. |
| `issuer_ir.consensus` (LSEG) | `lseg.com/en/investor-relations/consensus` | HTTP 200, one `.xlsx` link (`lseg-consensus-summary-13-august-2026.xlsx`) | available | A dated, versioned file is linked. This is a company-compiled consensus, not a market-wide panel. |

## Source specifications

| Source key | Jurisdiction | Auth | Cost | Rate limit (planned) | Formats | Publication lag | Owner | Technical stop condition |
|---|---|---|---|---|---|---|---|---|
| `sec.submissions` | US | none, User-Agent with contact address | free | 8 req/s (SEC ceiling 10) | json | minutes | owner | HTTP 403/429 after the declared User-Agent and ceiling are respected |
| `sec.archive` | US | none, User-Agent | free | 8 req/s | html, xml, txt, pdf, json | minutes | owner | archive URL not stable for an accession, or fair-access block |
| `sec.companyfacts` | US | none, User-Agent | free | 8 req/s | json | up to 24 h | owner | companyfacts disagrees with the filed instance beyond rounding |
| `sec.fsn_bulk` | US | none | free | 1 req/s | zip, tsv | up to 45 days (monthly/quarterly) | owner | a monthly set cannot be reconciled to the filed version |
| `sec.nport_bulk` | US | none | free | 1 req/s | zip, tsv | about 60 days | owner | quarterly set lacks the matched funds |
| `esef.filings_xbrl_org` | EU | none | free | 2 req/s | json, xbrl, zip | about 1 day | owner | package unavailable for a corpus issuer |
| `fca.nsm` | GB | none (public search) | free | 1 req/s | html, pdf, json | same day | owner | no public read route (only the Primary Information Provider submission API) |
| `companies_house.api` | GB | free API key (Basic) | free | 2 req/s (ceiling about 600 per 5 min) | json | same day | owner | key refused, or the limit is below the pilot's need |
| `edinet.api` | JP | free registration, API key | free | 1 req/s | json, zip, xbrl | same day | owner | no free key, or taxonomy/identity mapping fails |
| `ted.search` | EU | none | free | 1 req/s | json | days | owner | search cannot target buyers/suppliers by name |
| `gleif.level2` | global | none | free | 2 req/s | json | daily file | owner | relationship records absent for corpus issuers |
| `sedarplus.portal` | CA | portal session (registration if required) | free | 0.2 req/s | html, pdf | same day | owner | no stable document ID, incremental discovery or export |
| `issuer_ir.consensus` | per issuer | none | free | 0.5 req/s | html, pdf, xlsx | per issuer | owner | no versioned file or no metric definitions for the issuer |

## Registration notes

- **EDINET**: the FSA's EDINET API v2 requires a subscription key. Registration is free; no key was available when this was written. Until one is set, `edinet.api` stays `untested` and Japanese pilots are blocked, which is an access gap and not a data verdict.
- **Companies House**: the key in `.env` works for the company profile endpoint. Persons-with-significant-control records are company-control data, not listed-security holdings, and contain personal data: handle under the plan's P3.5 rules.
- **SEC**: `SEC_USER_AGENT` must contain a contact address; the fetch governor refuses to send a request otherwise.

## Known gaps to close before enabling a collector

1. SEC Financial Statement and Notes dataset file name (404 above).
2. FCA NSM real public search request shape.
3. EDINET free key.
4. SEDAR+ browser-session test: search, export, document ID stability, polling.
5. Shell consensus page: where the versioned file actually comes from.
6. SEC conditional fetch: the submissions JSON sent no validators; test the archive documents (`.htm`) for `ETag`/`Last-Modified`.
