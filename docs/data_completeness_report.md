# Data completeness report

Measured 2026-10-06 against the production database, 30 holdings: 27 operating companies and
3 funds (BRNT, SXRS, SXRV: an oil ETC and two ETFs, for which most company data does not
exist). Source: per-table coverage queries over `public.*` (holding counts, not row quality).

## Verdict

Not everything that can be filled is filled. The structured data (statements, fundamentals,
peers, shares, filings, ownership) is largely in place for the 24 SEC-filing holdings. Four
areas are largely empty: earnings facts, thesis verdicts, IR sources and releases, and the
web-researched slots. The web research was blocked until today by three faults (below), all
fixed in code; its results are pending.

## Update 2026-10-07 (supersedes the 2026-10-06 table where it differs)

All fixes below are deployed (last commit `c804420`; the 13F migration `form13f_dataset_tickers`
is applied in production).

| Data | Now | Change and cause |
| --- | --- | --- |
| CIK | 24 | Meta's CIK now written from the profile |
| Institutional holdings (13F) | 24 | Nu's CINS read from its Cayman ISIN; AstraZeneca's ADR matched by issuer name; a data set is re-read when a holding is added |
| Next report date | 21 | no change; see "Report dates" below |
| Relationship disclosures | 17 holdings, 100 rows (57 confirmed, 43 suggested then sent to AI approval) | `exposure_build` re-run on the fixed search; AZN 29, TSM 2, VRT 1 |
| FMP profiles / estimates | 24 / 13 | the estimates gate no longer stops other holdings' profiles; 11 holdings are not covered by the free plan, the other 6 are non-US listings |
| IR pages | owner-entered for BA, CELH, FB, GOOGL, NFLX, AAPL, NVDA, ORCL, UBER, VRT; discovered for PM, AZN, UNH, TSM | sites that refuse the worker's cloud address are read from the owner's machine (`uv run python -m worker.backfill ir_releases`) |
| Earnings facts | 26+ holdings (TSM 20 and Daiichi 16 figures now verified; Meta 18) | TWD/NT$ currency markers and fiscal-year relabelling |

### What was wrong, and is fixed

1. IR discovery and gap research answered "none" for most holdings. Gemini's batch mode
   returns no grounding sources, so the search now runs as direct calls and only the
   structured answer is batched.
2. A search answer with no sources was cached for 3 days and replayed. It is no longer
   cached; 40 such entries were deleted.
3. The FMP estimates gate was closed by a one-off HTTP 400, which stopped the loop and left
   profiles for 3 holdings.
4. The CNB press feed moved (old address returned 404).
5. Claude is never called; any `claude-*` model in the config fails the job.

### Report dates (ASML, AZN, TSM, QNT)

The search found them on the companies' own sites: ASML 2026-10-14, TSM 2026-10-15,
AZN 2026-10-30. None can be approved, because approval loads the page and finds the quoted
text: AstraZeneca returns HTTP 403 to scripts, ASML's site times out, TSMC's calendar is
built by JavaScript so a plain fetch lacks the quote. QNT has no published date. These need
an owner-entered date or a browser-rendered check.

### Still open

- `weekly_digest` has never succeeded (one run, interrupted by a restart on 2026-09-27); it
  emails the owner, so it has not been triggered. `monthly_review` succeeded (report 4).
- ASML, Broadcom and Quantinuum IR sites refuse connections even from the owner's machine.
- No relationships for FB, GE, GOOGL, NFLX, NU, ORCL, QNT, UBER: their filings probably name
  no customer or supplier concentration.
- News stories (18 of 30): mostly the sources carry nothing for the rest. TSM and UBER hold
  70 and 46 items that are other companies' 8-Ks matching "tariffs", correctly irrelevant.
- `EDINET_API_KEY` for CSG, Chugai and Daiichi.
- The unexplained worker restarts (possible out-of-memory with the browser fallback).

## Coverage on 2026-10-06 (holdings covered, of 30)

| Data | Covered | Missing | Why / status |
| --- | --- | --- | --- |
| Instrument identity (ISIN, currency) | 30 | none | complete |
| CIK | 23 | BRNT, CSG, Chugai, Daiichi, SXRS, SXRV, Meta | non-SEC issuers and funds; Meta's profile has `sec_cik` but `instrument.cik` was never written (fixed in code, not yet deployed) |
| Financial statements | 24 | BRNT, CSG, Chugai, Daiichi, SXRS, SXRV | no SEC filings. Japanese issuers need EDINET (`EDINET_API_KEY` still outstanding) |
| XBRL filings, forward observations, peers | 24 | same six | same cause |
| Fundamentals | 27 | Chugai, Daiichi, SXRV | |
| Shares outstanding | 24 | same six as statements | the web slot `shares_outstanding` targets these |
| Insider filings | 22 | ASML, Meta, six non-US | foreign issuers; Meta's empty `instrument.cik` (fixed in code, not yet deployed) |
| Institutional holdings (13F) | 22 | AZN, Nu, six non-US | foreign private issuers |
| Next report date | 20 | ASML, AZN, QNT, TSM, CSG, Chugai, Daiichi, BRNT, SXRS, SXRV | foreign issuers; the web slot `next_report_date` targets these |
| Relationship disclosures | 14 | 16 holdings | |
| News story clusters | 18 | 12 holdings | |
| IR sources registered | 8 | 22 holdings | only issuers with a probed or discovered IR page |
| IR releases parsed | 4 | 26 holdings | follows from IR sources |
| Research observations | 3 | 27 holdings | |
| Earnings facts (extracted figures) | 1 | 29 holdings | the check has run once (CSG, 14 verified facts) |
| Thesis verdicts | 0 | all | no theses config is saved, so none can be computed |

## Web-researched slots

Gap research fills `company_description` (29 open), `next_report_date` (10) and
`shares_outstanding` (6): 45 open gaps over 30 holdings, 8 filled per run. Attempts before
today: 26 errors, 13 "none", 1 approved. Causes, all fixed:

1. Provider keys missing on the host (fixed by setting them).
2. Supabase's session pooler allows 15 clients; the worker (10) plus the API (5) used all of
   them, so slots failed with `EMAXCONNSESSION` (worker pool lowered to 7).
3. Gemini returns no grounding citations when a search and a schema share one call, so every
   URL it found was rejected as "not among the search results". Now two calls (search, then
   structured answer), for direct and batch requests; verified live on both paths.

Result of the first run on the fixed code (job 459): attempt 1 was interrupted by a worker
restart; attempt 2 succeeded. It researched 8 of the 45 open gaps and approved 2 (Chugai
`next_report_date`, CSG `company_description`); the other 6 found nothing usable. 37 gaps
remain, so about five more runs are needed.

## What did fill today

Exposure geography, products, commodity inputs and currency exposures; supplier and customer
dependencies with confirmations; peer decisions (for example Boeing: GD, LMT, NOC confirmed);
IR page recheck (14 sources). Unconfirmed: Japanese listings (Chugai, Daiichi) remain
"left suggested" at confidence 0.4.

## Job health (last 7 days)

All scheduled jobs have a recent success. Failures were early first runs (`reference_data`
duplicate alias, `valuation_refresh` type error, both since fixed) and worker restarts that
interrupted `insider_trades`, `price_history` and `news_ingest`. The restarts at about 16:11
and 17:09 UTC are unexplained: no deploy coincided and the worker code has no self-exit.
Railway logs for those times (out-of-memory kill or platform restart) would settle it.

## Not data gaps

BRNT, SXRS and SXRV lack statements, shares, peers, filings and ownership by nature.
Fund look-through (`etf_holding`, `nport_fund_map`) is a separate dataset keyed by fund.

## Recommended next steps, in order

1. Read job 459 and re-run gap research about six times (8 slots per run) until the 45 gaps
   close; the remainder will be genuine "not found" cases.
2. Run the earnings check for the 29 holdings with no facts, against their latest reported
   quarter (one at a time through the job's `ticker` and `url` parameters), then save a
   theses config so verdicts can be computed.
3. Set `EDINET_API_KEY` so Chugai and Daiichi get statements, shares and peers.
4. Deploy the Meta CIK write-through, then run the insider job and Meta's earnings check.
5. Register IR pages for the holdings without one. IR discovery (jobs 492-494) found
   nothing for UNH, VRT and the three ETFs, and did not touch Chugai or Daiichi.
7. Resolve the two quarantined earnings checks (see below).

## Earnings checks of 2026-10-06 (jobs 471-498)

25 checks ran, all succeeded as jobs. 551 figures were stored as verified (23 holdings) and
36 were rejected by validation (2 holdings). No figure is left unverified.

- Located by EDGAR: 20 US holdings and ASML. Located by explicit URL: Nu, AstraZeneca,
  Chugai, Daiichi (the lookup now also covers these without a URL, see the plan).
- Gross profit was not found for GE, Alphabet, Intuit, Netflix, Oracle, Uber, UnitedHealth
  and Vertiv; the document does not print it. ASML and Nu lack operating income and diluted
  EPS, Philip Morris lacks net income.
- **TSM: 20 figures rejected**, "currency: TWD does not appear in the document". The
  release probably writes NT$; the currency check needs that spelling.
- **Daiichi: 16 figures rejected** by period alignment: Daiichi names its fiscal year by the
  year it starts (FY2026 Q1 ends 2026-06-30), the validator expects the year it ends
  (2025-06-30). Needs a per-holding fiscal-year naming setting or a validator rule.
- No figures came from XBRL: none of the documents was a 10-Q or 10-K.
- Thesis verdicts were skipped for every holding: no theses config is saved.
6. Pull Railway logs for 16:11 and 17:09 UTC to explain the worker restarts.
