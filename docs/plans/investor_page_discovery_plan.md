# Gap-filling research (investor pages and other missing data) — plan

## Status (2026-10-06)

Built: `ir_discovery` job (`fundamentals/ir_discovery.py`, table `ir_discovery_decision`);
`gap_research` job with the gap registry (`research/gaps.py`), source tiers, mechanical checks,
AI approval and `web_fact` / `web_fact_attempt` storage (`research/web_facts.py`); owner API to
list and overturn facts (`api/web_fact_routes.py`); results pages registered in
`issuer_ir_source`; job settings on the jobs form.

Slots implemented: `next_report_date` (applied to the earnings calendar with source
`web:<host>`), `shares_outstanding`, `company_description`, `annual_revenue` (statement figure:
needs an ISO currency, may be printed scaled, e.g. "1,234.5 million", never accepted from the
press alone; off by default, enable it in the gap research slots); `analyst_estimates` is
recorded as unavailable. Also built: robots.txt and rate-limited fetching (`research/polite.py`),
approver prompts in the prompts config, the web-facts panel on the holding page, and the
`/ai-decisions` page (what the AI approvers decided in the last 7/14/30 days, each with a
one-click reject), plus safety tests (hostile page text, concurrent owner edit).

Not built, by decision: Playwright (no holding is recorded as blocked), a multi-valued segment
slot (a `web_fact` holds one value; segment revenue comes from XBRL), and the AI promoting an
unlisted domain to a tier (unlisted domains are rejected).

Goal: find what the structured sources do not provide, starting with each holding's
investor-relations (IR) pages, and fill it from reliable web pages. Web search is the LAST
resort, used only for slots the structured sources have left empty. Everything found is
approved by AI against written reliability rules, stored with its evidence, and kept apart
from structured data so it can never silently overwrite it.

## What exists today

- Holding profile fields `ir_url`, `results_urls`, `financial_calendar_url`, `extra_domains`
  (`config/holding_profiles.py`), edited by hand and saved as a versioned `holding_profiles`
  config.
- `earnings_due` / `earnings_check` find the newest results document by Claude web search
  restricted to the profile's domains (`earnings/locate.py`), extract figures
  (`earnings/extract.py`) and verify each against the source text.
- `ir_releases` polls registered pages (`issuer_ir_source`), versions them and parses
  guidance and consensus tables (`fundamentals/ir.py`).
- Structured collectors: SEC XBRL, ESEF, Finnhub, FMP, Alpha Vantage, Twelve Data, Yahoo,
  13F, N-PORT, EDINET (list only), GLEIF, Wikidata.

Missing: (a) step 0, finding the IR pages (27 of 30 holdings have none), and (b) a general
way to fill any other slot the structured sources leave empty.

## Design

### 1. Gap registry (what is missing, per holding)

A pure function `find_gaps(holding)` lists empty **slots**, using the same checks as the
holding page's panels: IR pages, next report date, statement history, estimates, segments,
peers, ownership, corporate actions, company description, and so on. Each slot declares:

- its **structured sources in priority order** (e.g. next report date: Finnhub, IR calendar,
  exchange calendar);
- whether it is **fillable from the web at all** (numbers a reliable page prints: yes;
  anything needing a paid licence or login: no, recorded as `unavailable` with the reason,
  e.g. "FMP estimates need a paid plan", so it is not searched again for);
- how it is stored (below).

Slots that need the owner's own input (exposure assumptions, confirmed customer
dependencies, theses) are never web-filled; they are listed as owner tasks.

### 2. Order of attempts (web is last)

1. Structured API / regulator feed / filing (existing jobs).
2. The company's own IR pages (discovered by this job, then read by `ir_releases`,
   `earnings_check`).
3. **Web search**, only if 1 and 2 left the slot empty AND the slot is fillable AND its
   cooldown has passed (default 30 days between attempts, so a slot nothing can fill stops
   costing money). Searches are not restricted to the company's site; they are restricted to
   pages that pass the reliability rules below.

### 3. Source reliability rules (the part the AI approver applies)

Reliability is a **tier** stored with every fact:

| Tier | Examples | Use |
|---|---|---|
| T1 primary | the company's own site and filings; regulators and registers (SEC, EDINET, ESMA, national registers); exchanges and index providers; central banks and statistics offices | Accepted alone |
| T2 reputable secondary | established financial press and wire services (Reuters, Bloomberg, FT, WSJ and equivalents), reputable national press, established data providers with a stated methodology | Accepted for numbers only when a second independent source agrees, or a T1 source confirms; accepted alone for dates and URLs that the mechanical checks verify |
| T3 reference | Wikipedia / Wikidata | Names, identifiers, descriptions only. Never a financial figure |
| Rejected | forums, blogs, social media, anonymous or AI-generated content farms, aggregators that do not name their source, pages that cannot be dated, sites on the editable deny list | Never used |

The allow list (T1/T2 domain patterns) and deny list are editable config; the AI approver
may also place an unlisted domain in T1/T2 only with a written reason ("this is the issuer's
own domain: it matches the GLEIF website"), and unlisted domains default to rejected.
Approval always cites the evidence: the URL, a short verbatim quote and the retrieval time.

Hard checks before any AI judgement: https; resolves; not a login/consent/paywall page; the
quote is actually present in the fetched page text; the page date is known; the figure and
unit in the quote match the stored value. A search-result URL that was not returned by the
search is discarded (hallucination guard, as in `earnings/locate.py`).

### 4. Approval and storage

- A second Claude call (no search, cheaper model) judges each candidate: is the source
  reliable under the tier rules, does the quote support the value for this company and
  period, is it the right entity (not a subsidiary or namesake). Page text is untrusted
  data; the answer schema is fixed, so injected instructions cannot change it.
- Auto-approve: mechanical checks pass AND the AI approves AND (T1, or T2 with a second
  agreeing source) AND confidence >= threshold (config, default 0.8).
- Everything else is stored as `needs_review` (visible, never applied, never used in ratios).
- Approved values go to a new `web_fact` table: holding, slot, value, unit, period, source
  URL, tier, quote, retrieval time, verdict and reasons, `origin = 'web_search'`. They are
  **never written into the structured tables**; panels read them as a labelled fallback
  ("from reliable web source, not a filing") only where the structured slot is empty. A
  structured value arriving later supersedes the web fact automatically.
- IR page URLs found this way are merged into the holding profile through `save_config` (a
  new version, with `expected_active_id` so a concurrent owner edit wins; owner-set URLs are
  never overwritten) and registered in `issuer_ir_source`.
- Every decision is audited in `web_fact` / `ir_discovery_decision`, and rollback is restoring
  the previous config version or deleting the fact.

### 5. IR page discovery (first slot)

For each holding without `ir_url`: ask Claude (web search) for the IR home, results and
calendar pages, check them mechanically (host matches the issuer website from GLEIF /
Wikidata or an alias; reachable), AI-approve, apply. Weekly re-check drops pages that stop
loading.

### 6. Limits and responsibility

- Do not copy pages: store the figure, a short quote and the URL, not full text.
- Respect robots.txt and per-host rate limits; no logins, no paywall or bot-detection
  circumvention, no stealth tools.
- Numbers from the web feed the same verifier as earnings figures before they can drive an
  alert or a thesis verdict. Estimates / consensus that only paid vendors publish remain
  `unavailable` unless a T1/T2 page prints them with attribution.
- Cost: the search-enabled call is the expensive one and runs only for empty, fillable,
  off-cooldown slots; the approval call is small. Each job has its own cap in the budget
  config, inside the monthly $25 cap, and a hard per-run limit on searches.

## Approval policy for the whole app (AI decides, the owner can overturn)

No decision in the app waits for a human click. Everything that is currently "suggested" or
"needs the owner" is decided by an AI approver, recorded, and reversible:

- Applies to: web facts and IR pages (above), listing confirmation (currently
  `instrument_security_link.status = suggested`), peer suggestions (`peer_set.status =
  suggested`), contradiction/deal/relationship suggestions in the graph, and similar queues.
- A decision stores: who decided (`ai:<model>` or the owner), the evidence consulted, the
  reasons, confidence, and the policy version. Hard mechanical checks (identifier agreement,
  currency, host match, quote present) come first; the AI only approves what passes them, and
  anything it is unsure about stays `suggested`, not applied.
- The owner can overturn any decision in the app (one click: reject or re-confirm). An owner
  decision is final: no job or AI run ever overwrites it, and the AI never re-suggests what the
  owner rejected.
- Thresholds (confidence, tiers) and the list of auto-approved queues are config, so the owner
  can switch a queue back to manual.
- Rollout: AI approval is added queue by queue; each queue gets a weekly summary of what was
  auto-approved so overturning is easy.

## Blocked pages and Playwright

Checked 2026-10-06 with a plain request and a descriptive User-Agent: CSG
(`investors.csg.com`), Chugai and Daiichi Sankyo IR pages all return 200 with full content.
No robots.txt restrictions were published.

Fetch tiers:

1. Plain HTTP (current). Default.
2. Playwright (Chromium) fallback, only when tier 1 returns 403/429, an empty JavaScript
   shell (no links in the HTML) or a bot-challenge page. Not installed today. Cost:
   `playwright install --with-deps chromium` adds roughly 300–500 MB to the Railway image and
   ~300 MB RAM per browser; one page at a time, worker only. Same rules as above: robots.txt,
   SSRF guard, rate limit. A site that still blocks is recorded as `blocked` and left to the
   owner.
3. Never: logins, paywalls, anti-bot evasion.

Decision: build tier 1 now; add tier 2 only if a real holding needs it.

## Changes by area

Backend
- `research/gaps.py` (new): slot definitions and `find_gaps`.
- `fundamentals/ir_discovery.py` (new): IR page discovery, checks, approval, apply.
- `research/web_facts.py` (new): reliability tiers, candidate search, approval, storage.
- `config/jobs.py`: `ir_discovery` and `gap_research` schedules + settings (models,
  confidence threshold, cooldown days, searches per run, allow/deny lists, `enabled`).
- `config/budget.py`: per-job caps. `jobs/registry.py`, `worker/catchup.py`: register.
- Migration: `web_fact`, `ir_discovery_decision`.
- Prompts: `ir_discovery_search`, `web_gap_search`, `web_fact_approve`, registered with the
  prompts config so they can be overridden.
- Read side: panels that support a fallback read `web_fact` and label it.

Web
- `lib/config/jobs-form.ts` (+ test): the new jobs' settings and the allow/deny lists.
- Regenerated `openapi.json` and `lib/api/schema.d.ts`.
- Config page: "found by web research" list (approved / needs review) with a reject button;
  a "web source" badge on panels that show a fallback value.

Docs: `docs/data/provider-access.md`, the setup guide's job table.

## Tests

- Search result URL not returned by the search → rejected.
- Quote not present in the fetched text, or figure/unit mismatch → rejected.
- Unlisted domain without a written reason; deny-listed domain → rejected.
- T2 number without a second agreeing source → `needs_review`.
- Wikipedia figure for a financial slot → rejected (T3 is identity-only).
- Structured value present or arriving later → web fact not shown / superseded.
- Slot marked `unavailable` or inside its cooldown → no search, no cost.
- Owner-set profile URL never overwritten; concurrent edit → no write.
- Prompt-injection text in a page cannot change the verdict schema.
- Registry/catch-up/jobs-form consistency tests (they enumerate job types).

## Rollout

1. Build with `enabled: false`; run on demand for CSG, Chugai and Daiichi and compare with the
   hand-verified pages in config versions 14–17.
2. Enable IR discovery for the 23 US-listed stocks; review `needs_review` items.
3. Enable gap research for one slot at a time (next report date first, then segment and
   statement figures), reviewing results each time.
4. Schedule weekly. Revisit Playwright only if a holding is recorded as `blocked`.
