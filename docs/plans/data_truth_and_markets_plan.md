# Data truth, the autonomous engine and the Markets section

Measured 2026-10-16 against production (www.stewardai.cz, API api.stewardai.cz, Postgres on
Railway) after merge `bddee229`. Every finding below was read from the live database or the live
pages (a scripted walk of 81 routes plus the holding drawer); nothing is inferred from the plans.
UI work follows `ux_ui_final_redesign_plan.md` (FP) for scope and `ux_ui_visual_spec.md` (VS) for
visual detail, built with the frontend-design skill inside the existing design system.

Status: plan. Nothing below is implemented.

## 0. The short answer

1. **Some figures in the app are wrong, not just missing.** The Overview's "Return this year
   +62.2%" is a formula defect (section A1); the true flow-adjusted return since January is in
   the low teens. Boeing is forecast to pay a dividend it suspended in 2020. Every news-brief
   line says "0.0% exposure" for positions you hold. These are fixed first.
2. **The autonomous engine settles nothing because every kind runs in `shadow` mode** and the
   only way out of shadow is 50 owner-reviewed decisions per kind, of which there are zero. The
   judges it does run defer by default (no reject path) and are fed citations that cannot
   support a verdict (a bare Wikidata QID). Most backlogs (233 dividends, 2,718 filing hits,
   30,736 filing values, 402 relationships, 43 peers) have no judge running at all.
3. **Thin data is mostly jobs that stop early**, on the app's own quota guards or blocked
   provider endpoints, and stay stopped: dividends for 9 of 19 payers, ETF holdings for 0 of 3
   funds, 13F manager books for 0 managers, statements for 3 years instead of 10.
4. **The portfolio's quantities match Trading212**, but its classification does not: AstraZeneca
   is marked terminated, Celsius is French, the NASDAQ 100 ETF is Swiss, both Japanese holdings
   are unconfirmed, and no instrument has a type or exchange.
5. **The Markets section shows the wrong things**: the macro page charts revisions instead of the
   economy, the news feed is 79% "Other" with sports and unrelated stories linked to holdings, 13F
   lists 9 managers with mismatched figures, and filings show duplicate rows and unreviewed values.

## A. Figures that mislead today (fix first)

| # | Where | What is wrong (evidence) | Cause | Fix |
| --- | --- | --- | --- | --- |
| A1 | Overview "Return this year", benchmark chart ("ahead on 89% of days"), Performance rolling 90-day (−107%) | The account index jumps +45.6% on 2026-08-03 (CZK 10,000 deposited into a CZK 109 account), −2.0% on 08-17, −0.6% on 09-25. A return below −100% is impossible | `steward/portfolio/history.py:43` `flow_adjusted_index` divides by the previous total only: `(V1 − F)/V0 − 1`. A deposit that lands before the day's trading must enter the base: `(V1 − F − V0)/(V0 + F)`. The day's gain on fresh money (~CZK 50) is read as a 46% return on CZK 109 | Start-of-day flow convention (or Modified Dietz with the flow's time weight) in `flow_adjusted_index` and every reuse (rolling returns, contribution, `returns_refresh`). Tests with the three real deposit days. Overview must use the same coverage rule as Performance (Performance says "Unknown: 2.01% of value missing a price or flow"; Overview prints a number) |
| A2 | Income, holding drawer, company Dividend tab | Boeing forecast CZK 5.52 on 12 Nov 2026 (last Boeing dividend: Feb 2020). "Buy before" equals the payment date. Drawer: "Next payment USD 0.77" is CZK 0.77 (USD 0.036). Apple: "2 years without a cut" beside "Years without a cut 9" | Forecast repeats the last amount with no staleness rule; estimated ex-date is set to the pay date; drawer formats a base-currency amount with the instrument currency; the record check counts the partial first year (2016: 0.14) as a cut | A payer whose last payment is older than 2× its cadence is "suspended", never forecast. Estimated ex-date = pay date minus the payer's median ex→pay gap. Fix the drawer currency. Skip partial first and current years in cut counts. Drop the 36 rows of zeros from the income table |
| A3 | Company header, Valuation tab, holdings table, drawer | Apple header "Fair value USD 341.68, 0% above" (analyst consensus) vs Valuation "USD 243.3, 41% above" (multiple path, *unsaved*). Holdings/drawer print the raw string "no default dcf (filed growth) recorded" | No single rule picks the headline fair value; an unsaved draft scenario is shown as the value | One `headline_fair_value` rule used by all four places (saved default scenario, else stated fallback with its label); unsaved scenarios never headline. Missing value shows "—" with a plain reason (VS NullCell) |
| A4 | News brief | "The portfolio holds TSM_US_EQ with 0.0% exposure" on every line; ticker glued to the title ("TSM_US_EQTSMC…"); the same story twice in Spanish/German untranslated | Weight passed to the writer is 0 (to locate: `steward/data_context/portfolio.py` or the brief builder); title render concatenates; dedupe/translation runs before clustering | Pass position weight; render ticker as a chip; translate then dedupe by story id |
| A5 | 13F investors | State Street "$771.6B, 22 positions"; Vanguard "$0, 0 positions, Dec 2025" | Value is the filing total, "positions" counts only rows kept for your holdings (`form13f_manager_dataset` is empty) | Either load manager books (D4) or label the column "positions in your holdings" and show the overlap value, not the total |
| A6 | Drawer vs company page | Apple scores: drawer Past 4.5, Dividend 4.5; company page 5/6, 5/6 | Two score computations (half points for unknowns in one) | One score source |
| A7 | Holdings "Last event", drawer Updates, News feed | Apple Updates lists Travelers/Truist/Citizens 8-Ks "mentions tariffs"; KO's last event is a Nestlé story, AVGO's a MediaTek one; "Germany, Portugal reaffirm support for Ukraine" linked to CSG as *customer*; a Leeds football story in the feed | EDGAR full-text hits of *other* filers are attached to the holding (2,718 pending `filing_hit`); the graph linker fans one story out to every competitor/supplier/customer | Full-text hits count only when the filer is the holding or the holding is named within the matched passage; graph fan-out only above a relevance score and never for "customer" by country; sports/entertainment sources filtered at ingest |
| A8 | Filings | AMD 8-K of 28 Sep shown 5×, each "replaced by an amendment"; "TP ICAP (EUROPE)" in the company list; issuer names in capitals or Chinese | Re-ingests stored as new versions and read as amendments; a wrong LEI/issuer match; raw registry names | Dedupe document versions by content hash; remove the TP ICAP match and find which holding it came from; display names from `instrument.name` |

## B. Portfolio identity and classification

Quantities, average prices and values in `position_snapshot` match the Trading212 sync (30
positions, CZK 40,207, cash CZK 9.2). Before changing anything here I need to know which
position looked wrong to you (see "Decisions"), but these are wrong regardless:

| # | Holding | Wrong | Fix |
| --- | --- | --- | --- |
| B1 | AstraZeneca | `investment_universe.status = terminated`, `home_mic XLON`, prices from `azn.uk` (GBp) while you hold the US line in USD | The ADR was retired for a direct US listing; the lifecycle job read the ADR termination as the holding's. Mark active, link the GB ISIN to its US listing, price from the US symbol, re-run risk/price history for AZN |
| B2 | Celsius | country FR | Country from SEC state of incorporation/HQ (US); find which source wrote FR |
| B3 | SXRV, SXRS, BRNT | SXRV home CH/XSWX, identity *rejected*; SXRS and BRNT no identity; all typed "Mutual Fund" | Funds resolve by ISIN to domicile (IE, IE, JE) and asset type ETF, ETF, ETC; the identity matcher must accept funds without an operating issuer |
| B4 | Chugai (CUPd), Daiichi Sankyo (D4Sd) | identity *suggested*, score 0.40; no statements | T212 trades the Xetra EUR line; match by ISIN (JP3519400000, JP3475350009) to the Tokyo primary (4519, 4568) and confirm. Statements need EDINET (key, D9) |
| B5 | All 30 | `instrument_type` and `exchange` empty | Fill from the Trading212 instrument metadata the sync already reads |
| B6 | Allocation | Sector/region/asset-class mixes inherit B1–B5; ETF look-through empty (0 `etf_holding` rows) so SXRV counts as one "fund" slice instead of ~100 NASDAQ names | D6, then re-check /xray against the corrected identities |

## C. Why the autonomous engine leaves things for you

| # | Cause (evidence) | Fix |
| --- | --- | --- |
| C1 | `autopilot_settings.default.mode = shadow`, `kinds = {}`; promotion needs `promote_min_reviewed 50` owner reviews per kind; `auto_decision.review` is null on all 166 rows. Shadow decisions are recorded and never applied, so nothing leaves any queue | Per-kind modes: `apply` for reversible, evidence-bound kinds (dividend confirmation, filing-hit dismissal, identity by exact ISIN, relationship by structured registry, peer, web_fact, news_story corroboration), `escalate-only` for thesis and inbox actions. Keep revert payloads and the 5% spot-check sample; let calibration demote a kind whose spot checks fail |
| C2 | 97 `relationship` escalations: the judge receives "Wikidata reference + QID" with no statement text, so it defers even at 0.99 confidence | Render the Wikidata statement as evidence (subject, property label, object, retrieval date). Registry-sourced ownership (Wikidata P355/P749 corroborated by GLEIF) is confirmed by rule, no model call; "competitor" claims become peer *suggestions*, not relationships |
| C3 | `graph_disclosure`: clear rejections ("CISCO" painting contract in a French award, "non-US customers 46%") come back as *defer* | Give the judge a `reject` decision and make it actionable; defer only when evidence is genuinely missing |
| C4 | `graph_event`: "the citation is cut off" | Widen the snippet window to the full sentence(s) around the match |
| C5 | Backlogs with no decisions at all: `corporate_action` 233 suggested, `filing_hit` 2,718 pending, `disclosure_observation` 30,736 machine, `entity_relationship` 402 suggested, `peer_set` 43, `company_event` 6, `instrument_security_link` 2, `relationship_contradiction` 7 open | Zero-cost rules first: dividends confirmed when a Trading212 receipt, a second provider, or the filed annual DPS (already compared on the Dividend tab) agrees; filing values confirmed when they reconcile with `financial_statement_fact`; full-text hits dismissed per A7. Model judges only for what rules cannot settle |
| C6 | `draft_theses = false`, `research_max_per_run 1`, `draft_max_per_run 1`: no holding has a thesis, 63 case items stay drafts, GE reports on 19 Oct with no research | Turn on thesis drafts (owner approves each; they stay "draft" until you do), 5 per run, holdings by weight; research before every earnings date |
| C7 | `daily_cap 50`, `max_calls_per_run 60`, 3 autopilot runs since 15 Oct; AI spend is on course for $36.82 against a $25 cap | Rules (C5) clear most volume at zero cost; batch model calls; raise the cap only if you choose to (Decisions) |
| C8 | After C1–C5, close stale escalations and re-judge | One-off run, then the normal schedule |

## D. Data that is missing and can be had

| # | Missing (evidence) | Cause | Fix |
| --- | --- | --- | --- |
| D1 | Dividends for KO, PM, UNH, ORCL, NVDA, INTU, GOOGL, CUP, D4S | `yahoo_dividends` stops on "QuotaExceeded: free quota exhausted" (the app's own guard) after a few tickers; Twelve Data corporate actions blocked since 10-06 | Raise the internal Yahoo budget for this job and resume where it stopped; SEC XBRL DPS as second source |
| D2 | Apple 3-year revenue CAGR, ROIC, FCF yield, net debt/EBITDA all "Unknown"; statements only FY2023–TTM | Only three fiscal years loaded; operating cash flow missing (FP §7 D1) | `sec_fsds_backfill` to 10 years for all SEC filers; fix the cash-flow mapping; ESEF for CSG; EDINET for Japan |
| D3 | Fair value for ORCL, CELH, PM, VRT, NU, CSG, CUP, D4S, QNT, funds | No saved default scenario (A3) and inputs missing (D2) | After D2, run `valuation_refresh` and save default scenarios |
| D4 | 13F manager books; ownership last loaded 10-06 | `form13f_manager_dataset` 0 rows; `ownership_13f` timed out on 10-10 and was not retried | Load full books for tracked managers, batch the load, schedule retries; add managers that hold your stocks |
| D5 | Macro page shows ~11 of 42 stored series; no US GDP, ISM/PMI, payrolls, 2-year yield or curve, dollar index, VIX, credit spread (`us_hy_spread` is read by the sector map but never collected), Japan | Page built for revisions; collectors not configured | Add the FRED series; Japan via FRED/OECD |
| D6 | ETF holdings for SXRV, SXRS (0 rows) | `finnhub/etf_holdings` blocked | iShares publishes holdings CSV per fund; read it weekly |
| D7 | IR releases for AZN, ASML, AVGO, PM, CIEN, CSCO | No IR source discovered | IR discovery per holding; owner-machine read where sites block cloud IPs (existing path) |
| D8 | Business description, employees: "not stored" | FMP profile only covers part | SEC 10-K Item 1 / Wikidata |
| D9 | Japanese filings, patents, web search, phone alerts | Keys missing: EDINET, EPO OPS, Brave, Telegram | You add them (free); see Decisions |
| D10 | Monthly review: no run for 233 hours | Schedule/registration (to locate) | Fix and run once |
| D11 | Six job steps stuck "running" since 10-06/10-09 under failed jobs | Failed jobs do not close their open steps | Close open steps on job failure; clean the six rows |

## E. Interface (frontend-design skill; FP and VS)

FP §11 says R0–R8 are code-complete but were never measured against live data. The walk shows
where the result falls short:

- **E1 Markets.**
  - *News* (FP §5.10): fix the classification taxonomy first (79% "Other"); the default
    feed shows only stories linked to holdings with a relevance score, grouped by day and
    holding, typed chips with counts, Ignored hidden.
  - *Brief* (FP §5.11, VS §C.6): open with "what moved your portfolio" (top contributors,
    with weights), then per-holding lines sorted by weight, then this week's earnings,
    ex-dividend dates and macro releases; fix A4.
  - *Macro*: a dashboard of small multiples (VS B30) grouped Rates / Inflation / Growth /
    Labour / Commodities / FX, countries chosen by your exposure. Each card shows the latest
    value, the change, a 1y/5y/10y range and a plain sentence. Revisions move into the
    detail view.
  - *13F*: start from your holdings ("who owns what you own, and who added"), then the
    manager pages (treemap B17, change bars B15).
  - *Filings*: per-holding filing timeline with deduped documents and readable names; the
    review queue disappears once C5 settles it.
- **E2 Holding drawer** (VS B38): price sparkline with the fair-value band, a thesis line or
  AI draft, next catalysts (earnings date, ex-dividend), 3–4 KPIs with verdicts, and updates
  limited to real news about the company. No raw internal strings.
- **E3 Company pages** (FP §5.3, VS §C.2): replace tables with the specified charts.
  - Financials: revenue/earnings bars with the table as a "table twin".
  - Segments: donut plus stacked area over time.
  - Ratios: history lines against the 5-year median.
  - Valuation: DotStrip of every fair value plus the FanBand of analyst targets.
  - Governance: holder-type stacked bar (B29) and insider lollipops.
  - Health: paired asset/liability bars and the debt ladder.
  - Each keeps the table twin for accessibility.
- **E4 Consistency:** after A1–A3, one number per concept across Overview, Performance,
  Holdings, the drawer and company pages.

## F. Setup

| # | Finding | Fix |
| --- | --- | --- |
| F1 | Railway tags every log line "error" (Python logs to stderr), and apscheduler writes INFO lines every 15 s, so real errors are invisible | Log to stdout with a level field; apscheduler at WARNING |
| F2 | The temporary database TCP proxy used for this audit | Removed 2026-10-16; re-added only while implementing, then removed |
| F3 | AI spend forecast above the cap | Decision below |
| F4 | Health is fine (`/health/ready` ok, worker heartbeat current, MFA on); FP §11 measurements (360/768 px, Lighthouse) never run | Run as part of the E verification |

## Order of work and how each step is checked

1. **A1–A8**, then a route walk to confirm the corrected numbers.
   - Unit tests for the index use the three real deposit days.
   - Overview and Performance must show the same return.
2. **B1–B5 data fixes**, then re-check /xray and Allocation against Trading212.
3. **C1–C5.**
   - Check: the "needs you" count and each backlog before and after, from the database.
   - Check: a 20-item spot sample of applied decisions that you can review in the Autopilot page.
4. **D1–D11.** Each job runs once, and its row counts per holding are compared with the coverage
   matrix taken on 2026-10-16.
5. **E1–E4**, one area at a time.
   - Each area gets a screenshot at desktop and 360 px.
   - The plan's spec items are ticked in FP §11.
6. **F1.** Commit per area, push to `main` (Railway and Vercel redeploy), then check the
   deployed site.

## Decisions I need from you

1. Which position or quantity looked wrong to you? Quantities match the Trading212 sync; the
   classification errors are listed in B.
2. Autopilot: may low-risk kinds (C1 list) apply decisions automatically, with spot checks and
   revert, instead of waiting for 50 reviews each?
3. Thesis drafts: may the AI draft a thesis for every holding? Each stays a draft until you
   approve it.
4. AI budget: keep $25/month (rules first, fewer model calls) or raise it?
5. Keys: add EDINET, EPO OPS, Brave and Telegram (all free) in Railway variables, or skip any.
