# News coverage plan: deep audit (2026-10-10)

Audited: `docs/plans/news_coverage_analytics_improvement_plan.md` and the code built for it (`apps/backend/src/steward/news/`, news routes, migrations 20261028100000 to 20261103100000, budget and jobs config, news chat tools, the news web pages).
Method: four read-only reviews (analysis engine, ingestion and sources, API/migrations/budget/chat, web). Findings were read in context; the three Highs and the ai_search tier path were re-read a second time. Nothing was run. Status column is updated as fixes land.

Status: `open`, `fixed`, `rejected` (false positive), `deferred` (reason given).

## High

| # | Where | Issue | Status |
|---|---|---|---|
| H1 | `pipeline.py:1736-1760` | Only exceptions count as source failure. A collector returning errors with no items (missing SEC_USER_AGENT or DART key, all-holding 429s) is counted healthy: breaker reset, `last_success_at` set. A quiet page source is flagged "parser drift" after one busy run and the breaker opens. Breaks "outlet unreachable is never rendered as no news". | fixed |
| H2 | `primary_sources.py:483` | Gazette match is a substring test; "Next Day Couriers Ltd" links to "Next plc" as tier 1 regulator event and can alert. Plan says whole normalised name. | fixed |
| H3 | `pipeline.py:1736`, `normalize.py:28` | Only FetchError/HTTPError/ValueError caught per collector; AttributeError/TypeError from `parse_hkex`, `parse_dart`, CNINFO lookup kill the whole ingest. Bad port in `canonical_url` raises inside `store_items` with the same effect. | fixed |

## Medium: backend

| # | Where | Issue | Status |
|---|---|---|---|
| M1 | `budget_guard.py:151-167`, `config/budget.py:210-217` | PLAN MISMATCH: a monthly cap on `news_ingest` raises BudgetExceeded mid-run; plan says a capped stage degrades and never stops ingestion. No test of the guard against `per_job_monthly_cap`. | fixed |
| M2 | `ai_search.py:152,241` | Only `SearchLeadError` caught; BudgetExceeded escapes, the store step is skipped, leads lost, failed-job alert daily at the 2.5 USD cap. | fixed |
| M3 | `analysis_job.py:384-406` | If `explain_chains` raises, the code-only result is saved under the "explained" hash; never retried; row records a model that did not run. | fixed |
| M4 | `translate.py:85` `numbers_in` | Numbers glued to letters ("$12bn", "3x", "5m") not checked; separators stripped so "1.5" equals "15". Used by briefs, glosses, claim verdicts, extraction magnitude. | fixed |
| M5 | extraction, framing, thesis, impact, brief | PLAN MISMATCH "zero uncited claims saved": framing `headline_claim`/`emphasised_cause`/conflicts, thesis `reason`, impact notes and hypotheses, brief `why`/`watch` are saved without span check. | fixed: numbers are checked everywhere; sentences that report the text (framing headline claim and cause, brief line text) are also checked for content words in the sources (`ungrounded`, needs 3+ words). Reasons, hypotheses and conflict sentences are interpretation and get the number check only |
| M6 | `analysis_job.py:725`, `brief.py:92` | PLAN MISMATCH "sentiment never alerts": `alert_brief` picks top 3 by `score`, which includes the sentiment nudge. | fixed |
| M7 | `analysis_job.py:561-568` | PLAN MISMATCH lazy top-N: `pick_stories` orders by severity and `last_seen`, not exposure x materiality; a story with no items consumes an `analysis_per_run` slot; no on-demand analysis. | fixed |
| M8 | `extract.py:114-115` | Claim whose `item_id` is not a story item is span-checked against all items and kept with the wrong `item_id`. | fixed |
| M9 | `eval.py:225-266` | `--gate` without `--live` runs the oracle classifier against itself and passes vacuously; gate covers event type only, not relevance. | fixed |
| M10 | `market.py:133-145` | `event_day` is the UTC date; a story published after the close is credited with that day's move. | fixed |
| M11 | `market.py`, `SELECT_DAYS=3` | 3- and 5-day windows often incomplete; event study starved. | fixed |
| M12 | `novelty.py:41-52` | Embeddings of mixed dimensions raise IndexError; step catches only PostgresError/ValueError. | fixed |
| M13 | `link.py:150`, `link.py:131` | `\$700\b` matches "$700 million"; CJK name followed by a particle never matches. | fixed |
| M14 | `feeds.py:593`, `wire_sources.py:175` | GDELT/Google News name-only query hits tagged direct `source` link at confidence 1.0, no name matching. | fixed |
| M15 | `evidence.py:89` | `\bcorrection\b` matches "market correction" and flips lifecycle to `corrected`. | fixed |
| M16 | `news_sources.py:822` | `independence_key` does not reduce to a registrable domain; `markets.ft.com` and `ft.com` count as two sources. | fixed |
| M17 | `pipeline.py:593` | ETag committed before `store_items`; crash in between loses items on next 304. | fixed |
| M18 | `provenance.py:29` | Skipped rows stay null and `order by id desc limit 500` re-fetches them forever; also uses URL host, not `outlet_host`, so independence key differs from new items. | fixed |
| M19 | `fulltext.py:403-416` | Rows with missing policy or exhausted allowance stay in the candidate set and starve other sources. | fixed |
| M20 | `dedupe.py:97,182` | `recent_vectors` unbounded, O(new x all) in Python; `source_count` overwritten with raw outlet count ignoring independence groups. | fixed |
| M21 | `research/polite.py` `_robots_for` | A 3xx on robots.txt treated as no robots (allow-all); client does not follow redirects. | fixed |
| M22 | `chat/tools.py:542-573` | `_semantic_wire` ignores the `ticker` filter; other holdings' items returned. | fixed |
| M23 | `read_analysis.py:252`, `chat/tools.py` ~499 | OR of `@@` with `ilike` forces a seq scan of `news_article`; no retention. | fixed |

## Low: backend

| # | Where | Issue | Status |
|---|---|---|---|
| L1 | TDnet, HKEX | 20 pages / 3000 rows silently truncated; TDnet page 2+ errors swallowed. | fixed |
| L2 | `normalize.canonical_url` | Strips `cid`, `ref`, `mod`; distinct articles can collapse. Also accepts non-http schemes. | fixed: non-http schemes rejected; `cid`, `ref`, `mod` are stripped only when the value is not purely numeric |
| L3 | NSM matching | Names under 4 chars (BP, BT) and "P.L.C." variants never match. | fixed |
| L4 | `code_key` | Japanese alphanumeric stock codes collapse. | fixed |
| L5 | `store_items` | Duplicate URL keeps first source's tier, not best. | fixed |
| L6 | `store_items:951-1004` | Orphan `story_cluster` when item insert conflicts. | fixed |
| L7 | simhash grouping | Cross-run grouping skips older late arrivals. | fixed: any ungrouped item in the window can join an earlier copy, so a late older article groups with the newer copy from an earlier run |
| L8 | `threads.py:205-209` | `record_update` reopens closed threads; concurrent runs can duplicate threads. | fixed |
| L9 | `triage.py` | Items rejected by `check` stay untriaged and are re-sent for 48 h. | fixed |
| L10 | brief/alert text | Feed-controlled text goes into notifications unescaped/unbounded. | fixed |
| L11 | `news_routes.py:453`, `tools.py:614` | `story_id: int` not range checked; huge id gives 500; bad model id fails the whole chat job (`ToolBox.call` catches TypeError only). | fixed |
| L12 | `news_routes.py:457` | `put_incident_status` accepts any story and any `kind`. | fixed |
| L13 | ilike filters | Unescaped `%`/`_`; empty `query` in `search_wire` matches everything. | fixed |
| L14 | `.github/workflows/news-eval.yml` | Header says skipped without secret but `if` checks only the variable; secret exposed at job level; actions pinned by tag. | fixed |
| L15 | `theme_days` | DB UTC `current_date` vs Europe/Prague budget months. | fixed |
| L16 | `config/jobs.py` | `news_triage` has no default monthly cap (plan line 193 says each stage has one). | fixed |

## Web

| # | Where | Issue | Status |
|---|---|---|---|
| W1 | `wire-view.tsx:262`, `story-view.tsx:142,405`, `disputed-view.tsx:308` | Feed URLs rendered as hrefs without the `isExternalUrl` check used in `news-view`/`holding-news`. | fixed |
| W2 | `wire-view.tsx:70-79` | `more()` has no stale guard or error handling; `loadingMore` can stick. | fixed |
| W3 | `wire-view.tsx:136-147`, `analysis.ts` | Inputs trimmed per keystroke (cannot type "Nikkei Asia"), fetch per keystroke; `draftQ` not re-synced on back/forward. | fixed |
| W4 | `ops-view:30`, `brief-view:48`, `stories-view:30`, `story-view:55`, `news-view:292` | Failed requests rendered as empty/none-yet. | fixed |
| W5 | `brief-view:41-63`, `story-view:64-76`, `stories-view:84-92` | No cancel/sequence guards; no try/catch; `toggleClosed` ignores failure; ThreadCard error never clears. | fixed |
| W6 | ops, disputed, news-view | Old data under changed filter, no loading state. | fixed |
| W7 | `story-view.tsx:194,388` | `key={c.claim}` / `key={t}` collisions. | fixed |
| W8 | `jobs-form.ts:1318` | `analysis_techniques` not lower-cased. | fixed |
| W9 | storyDay/storyTime | Browser time zone, not market date. | not a defect: the price-reaction day is the owner local day by design; the backend uses a per-country close |
| W10 | citation spans | Plain text, not linked to the item. | fixed: quoted spans link to the evidence item on the Story page |

## Tests that give false confidence

| # | Where | Issue | Status |
|---|---|---|---|
| T1 | `test_news_n6_db.py:175-227` | Fake LLM returns empty framing/claims/notes; only extraction, novelty, thread rows asserted. No coverage of impact, framing, claims, thesis, market, themes saves. | fixed: framing, claim check and impact chains are asserted (impact chains come from a patched exposure graph; the model supplies only the sentence) |
| T2 | tests | No coverage of `resolve_quiet_stories`, `alert_brief`, `fetch_gdelt`, `write_theme_briefs`, `extract_batched`, sentiment job path. | fixed: tests added for alert_brief, fetch_gdelt, event_day_of, centroid, framing numbers, like_pattern, resolve_quiet_stories, theme narratives, sentiment job path, batched extraction |
| T3 | `test_news_n3_db.py:157` | Lead is wire-only so zero alerts is trivial. | fixed |
| T4 | `test_news_wire_db.py:136` | `health == "down" or backoff_until <= now` likely always true. | fixed |
| T5 | `test_news_n5_db.py:153` | Sets `indirect_only` by hand; link-derived path untested. | fixed |
| T6 | web `*.test.ts` | Pure helpers only; no error-vs-empty, stale response or URL sanitising tests. | fixed |
| T7 | budget | No test of the guard against `per_job_monthly_cap`. | fixed |

## Plan document inconsistencies

| # | Issue | Status |
|---|---|---|
| D1 | Lines 336, 351, 433 contradict line 405 and the code (jobs are on; `news_brief` 1.5 and `news_ai_search` 2.5 caps exist). | fixed |
| D2 | Line 278 "Not done: 6.11, news_triage" is stale. | fixed |
| D3 | Plan claims that remain overstated until M5-M7 are fixed. | partly: see M5 |

## Rejected (false positives)

| # | Claim | Why rejected |
|---|---|---|
| R1 | "ai_search lead becomes tier 1/2 through `item_tier` (`pipeline.py:736-749`)". | `ai_search.py` stores leads at tier 3 directly and `pipeline.py:1690` excludes `ai_search` sources from ingest, so `item_tier` never sees them; `evidence_kind` returns `lead`. |
| R2 | "Wire items could alert". | Stored with `linked=false`, no `news_item` row. |

## Not fixable in code (still open from the plan)

Live-model runs, keys (DART, EDINET), browser verification, human label review, real labelled benchmark, SEDAR+/Bundesanzeiger by decision.

## Resolution (2026-10-10)

Fixes by three worker passes (ingestion, analysis/budget/chat/api, web) and a plan-doc edit.
Checks run: backend news, jobs, chat, budget DB tests against the local Postgres container (77 passed for the targeted set; one stale mock in `test_news_n3_db.py` was fixed: `create_llm` takes one argument); `python -m steward.news.eval_analysis` 73/73; web `npm run typecheck`, eslint on the news, sources and config folders, vitest on `src/lib/news`, `config`, `sources` (the two pre-existing failures in `research-profiles-form.test.ts` and `verification-form.test.ts` were stale "gpt-5" fixtures and are fixed; the 5 `test_llm*.py` failures were stale after the Claude removal and are fixed too).
R1: the agent also moved the `ai_search` check first in `item_tier` as a defensive change; the original path was unreachable for ai_search leads, so the finding stays rejected as a live bug.
`news_triage` default monthly cap is 1.0 USD (a choice made in the fix; change in `config/budget.py`).
No commit was made.
