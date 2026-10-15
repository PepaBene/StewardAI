# Multi-provider model routing plan

Move AI work off Anthropic-only onto three tiers.

## Status (2026-10-06, local commit, not pushed)

Built: the provider interface `steward/llm` (`LLM.complete`, request/result types, OpenAI Responses and Google generateContent wire code, an Anthropic adapter with the `pause_turn` loop), usage normalised to Anthropic's shape (cached tokens billed once), the guard and per-step recording for every provider (`budget_guard.before_request` can move a call to a downgrade model of another provider only through `LLM`; a raw Anthropic call at the cap is paused instead), per-model cache and search prices with the three new models priced, `ModelId` accepting `gpt-`/`gemini-`, the `OPENAI_API_KEY` and `GOOGLE_API_KEY` settings, and news classification as the pilot call site (it uses the interface whenever its model is not a Claude model; the default is still Haiku).

Also built since: a routing client (`llm/routing.py`) that sends `gpt-`/`gemini-` models through the interface and leaves Claude models on the old path, wired into `exposure_build`, `ai_approvals`, the earnings calendar and news classification; the `ai_result_cache` table (migration `20261006160000`, not yet applied to the database) with a `ResultCache` that answers an exactly repeated structured call from the table, used by `ai_approvals`; and budget config version 25 with prices for the three new models.

Also built, uncommitted and not yet checked by any test, lint or type run (by the owner's instruction): the routing client wired into `ir_discovery`, `gap_research`, `news_escalation`, corroboration, `earnings_check`, `research` and the monthly review (a search call on another provider returns its citations in the shape of Anthropic's search-result block, so the source-tier checks run unchanged; another provider's failure is raised as `anthropic.APIConnectionError`, so every call site's existing skip and retry handling applies); app function tools in the interface (OpenAI function calls, Google function declarations, provider output items replayed for the next turn) and a non-Claude chat loop in `ClaudeChatAssistant`; batch through `LLM.complete_batch` for OpenAI (JSONL file, `/v1/batches` on `/v1/responses`, 24 h window) and Google (`batchGenerateContent` with inline requests), written from the vendors' documentation and never run against them: the request is cancelled and all answers are None when the batch is not done within `batch_wait_minutes`, the batch id is saved on the step as soon as it is submitted, each answer is None when its request failed, and the caller then asks again directly; news classification uses it for a run's whole backlog when `classification_use_batch` is on and the model is not a Claude one (off by default, so a severe item is labelled at once; a group the batch did not answer is classified directly); the digest and the monthly review use it for a non-Claude model, with the same fallback to a direct call as the Claude path; `PromptsConfig.override_providers`, so an override can be limited to some providers (a step on another provider then sends the built-in default; wired at the digest, claim verifier, chat, earnings, monthly review and news classification and corroboration call sites); cache hit rate per job type and spend per provider in `GET /costs/summary`.

The eval and golden-set scripts (`news/eval.py --live --model gpt-6-luna`, `verify/eval.py`, `earnings/golden.py`, `verify/evals_gate.py`) now take the routing client, so each gate can be run on any provider's model once its key is set; the gates themselves have not been run.

Already in the code before this plan, so not rebuilt: rule-based event types for EDGAR filings (form type and 8-K item, stored as `classified_by = rules:<source>`), story clustering that removes duplicates, polite page fetching, the market-suffix and symbol checks in `ai_approvals`, and the severity threshold for digest summaries.

Also written after that, equally unchecked: batch for the other jobs, all off by default (`use_batch`, `batch_wait_minutes`). `earnings_calendar` fetches the IR pages that need the AI and reads them as one batch. `ai_approvals` collects its prompts and sends them as one batch (`parse_many`). `ir_discovery` and `gap_research` run their holdings side by side with a `BatchCollector` (`llm/batching.py`): every flow reaches its next call, that round of calls is one provider batch, and the flows continue, so the existing search-then-judge code is unchanged; a call the batch did not answer is asked directly. Result caching: web-search calls are cached for 3 days in `ir_discovery` and `gap_research` (the cached entry keeps the result URLs, so the source-tier checks still run), and news classification caches by model, system prompt (which carries the holdings block), items and date. Rules before AI, each recording `decided_by`: listing approval settles an exact match (suffix, ISIN country and symbol all pass) or a clear mismatch (suffix and symbol both fail) in code (`AiApprovalSettings.rules_first`); IR discovery probes `/investors`, `/investor-relations`, `ir.<domain>` and `investors.<domain>` on the domains the profile names before it searches (`probe_first`); the claim verifier checks full dates against the snippets and accepts a verbatim run of a snippet without a model call (`date_check`, `quote_check`). Chat answers a short, closed question about the portfolio's value or cash from the `portfolio_overview` read model with a template (`chat/rules.py`, `ChatSettings.rules_first`): the answer cites a source the rule registers with the exact figures it states, so the usual figure check applies, and anything open-ended goes to the assistant. Digests already list the low-severity items from a template (`min_severity_listed`); only items at or above `min_severity_summarised` reach the model. Because `verify/claims.py` changed, the claims gate needs a live run before CI passes.

Checked against the code, and not needing new code: corroboration matching (`news/corroborate.decide` already settles a story from other stories on the same holding and event type, and a search runs only for a story still unconfirmed); research sections built from data (`financial_trend`, `valuation`, `ownership`, `relationships`, `risk` and `liquidity` are already written by code; only `business`, `moat_risks`, `strategy_fit` and `open_questions` call the model); earnings figures from XBRL (the filing's XBRL appears weeks after the results release, so it cannot replace the extraction at release time; `earnings/reconcile.py` already checks every extracted reported figure against it once filed, and a 10-Q or 10-K arriving as the document is the only case where XBRL could stand in, which needs the golden-set comparison first).

Built after that: peer approval confirms a peer in code when its SEC SIC code (`EdgarClient.sic`) equals the holding's and the profile names it as a competitor (`use_sic`); the SIC is also given to the model as evidence. Chat answers "when does X report next?" from `instrument.next_report_date` when exactly one holding is named (`chat/rules.py`). Google grounding citations are redirect links, resolved before the allowed-domain filter. OpenAI and Google count reasoning tokens against the output limit, so routing sets `max_tokens` to at least 8000 for them. The claim verifier prompt no longer treats a missing label (company, period, unit) as an unsupported claim.

Live results (2026-10-06): news classification on `gpt-6-luna` 99% (target 85%); the earnings gate sample on `gpt-6.1-sol` at high effort 36/36 fields; claims, with the clarified prompt, luna and flash both catch 20/20 injected errors and flag 0/20 faithful sentences (the old prompt flagged 4-7 of 20 on every non-Claude model). Chosen switch (`switch_models.py`, to be applied only after deploy, since the deployed worker accepts only `claude-` ids): sol for earnings extraction, escalation, review and the standard research profile; flash for earnings location, exposure, IR discovery, gap research, approvals, digest and the quick profile; luna at low effort for the calendar, news classification and corroboration, chat and claims, and as the budget downgrade model. Not yet run live: the OpenAI and Google batch calls; the sol claims eval with the new prompt.

Not built, and why: the cost-reduction settings that need the owner's numbers (monthly and per-job caps, per-job effort, lower search caps, schedule changes); the rule-based replacements that must be measured against the golden sets first (XBRL as the figure source for a 10-Q or 10-K document); the evals per provider and the gates; the verification calls in "Verify before building" (they need the keys).
| Tier | Model | List price (in / out per 1M) | Cached input | Web search |
|---|---|---|---|---|
| Hard | OpenAI `gpt-6.1-sol` | $2 / $10 | $0.10 | $10 per 1k |
| Middle | Google `gemini-3.8-flash` | $0.75 / $3.75 (doubles 2027-01-01) | $0.075 | $14 per 1k (Google Search) |
| Small + chat | OpenAI `gpt-6-luna` | $0.10 / $0.50 | $0.01 | $10 per 1k |

Batch is 50% off on both providers. Prices read from the vendors' pricing pages on 2026-10-06.

## Routing, job by job

Today's model in brackets. "Search" = uses a web search tool, the risky part of the move.

### Sol (hardest, highest stakes)

| Job / call site | Today | Why sol |
|---|---|---|
| `research` standard profile, `research/writer.py` (search) | Opus 5.5, high | 10 cited sections, peers, valuation; the deepest workflow |
| `monthly_review`, `review/monthly.py` | Opus 5.5, high, batch | Proposes portfolio actions; run once a month, keep batch |
| `earnings_check` extraction, `earnings/extract.py` | Sonnet 5, high | Numbers from long reports checked against theses; a wrong figure is costly |
| `event_escalation`, `news/escalation.py` (search) | Sonnet 5 | Decides what alerts you; rare, so the cost is small |

### Flash (in between)

| Job / call site | Today | Why flash |
|---|---|---|
| `daily_digest`, `weekly_digest`, `news/digest.py` | Sonnet 5, batch | Summarising many items; keep batch |
| `research` quick profile | Sonnet 5, medium | Short screen, 3 sentences per section |
| `earnings_check` locate, `earnings/locate.py` (search) | Sonnet 5 | Finds the release for non-US issuers |
| `ai_approvals`, `approvals/ai.py` | Sonnet 5 | Judges suggested listings and peers from given evidence |
| `ir_discovery`, `fundamentals/ir_discovery.py` (search) | Sonnet 5 | Finds and approves IR pages |
| `gap_research`, `research/web_facts.py` (search) | Sonnet 5 | Finds facts and approves them; sol if approvals look weak |
| `graph/patents.py`, `graph/ops.py` | unchecked | Review before assigning; default flash |

### Luna (small tasks and chat)

| Job / call site | Today | Note |
|---|---|---|
| `news_ingest` classification, `news/classify.py` | Haiku 4.5 | Highest volume; best first migration |
| news corroboration, `news/corroborate.py` (search) | Haiku 4.5 | Search; check luna handles the search tool, else flash |
| `earnings_calendar`, `earnings/calendar.py` | Haiku 4.5 | Reads dates off IR pages |
| claim verification, `verify/claims.py` | Haiku 4.5 | Strips or marks unsupported claims |
| `chat`, `chat/assistant.py` | Sonnet 5 | Your choice. It is a critical job with up to 30 tool rounds; watch quality |
| web domain tier, `research/domains.py` (added 2026-10-06, uncommitted) | gap-research model (Sonnet 5) | Input is only a domain and a company, never above T2, and "rejected" when unsure; each domain is judged once and stored in `web_domain_tier`, so volume is tiny. Luna is enough, but its knowledge of publishers is weaker, so test it on known good and bad domains |
| budget `downgrade_model` | Haiku 4.5 | Becomes luna |

## What has to be built

1. **Provider interface** in front of `steward/ai.py`: one `complete()` call taking messages, tools, a pydantic output schema and an effort level, returning a normalised result and `Usage`. Call sites stop calling `messages.parse` directly (22 `messages.parse`/`create`/`batches.create` calls across the backend as of 2026-10-06).
2. **Adapters**: OpenAI (Responses API), Google (`google-genai`); Anthropic stays for rollback.
3. **Budget**: `BudgetedAnthropic` only guards Anthropic. Move the guard into the interface so every provider is capped and counted. `_downgrade` is Anthropic-specific and needs a per-provider version. Keep the per-response recording added in commit `6769e50` (`record_response` in `jobs/runner.py`, called from `BudgetedAnthropic.post`): it saves each response's usage to its step row as it arrives, so failed or killed steps keep their cost. Every provider adapter must call it too, and map usage fields per provider (see Caching, accounting). Batch cost is still recorded only when the step finishes; a batch whose process dies is not costed, so record the batch id on the step when it is submitted.
4. **Price table**: `BudgetConfig` has one set of cache multipliers and one search price. Make both per model. OpenAI cached input is about 0.05x of input with no write premium; Gemini cache read is 0.1x; search is $10 or $14 per 1k.
5. **Config**: `ModelId` (`config/jobs.py`) is a string that must match `^claude-...`; loosen the pattern to allow OpenAI and Google ids (or use a `provider/model` form) and keep older saved config versions valid. The model price table (`config/budget.py`) needs the new models too; a model without a price gets a null cost, which the spend totals skip.
5a. **Prompts config** (`config/prompts.py`): 17 editable system prompts, one per AI step, written and evaluated for Claude. The CI evals gate fingerprints only the built-in defaults, and owner overrides are outside it. Decide per prompt key whether an override applies to every provider or only the one it was written for, and re-run the evals for each provider a step moves to.
6. **Structured output**: map `output_format=SectionDraft` to each provider's schema mode; keep the parse-failure handling.
7. **Search**: the `pause_turn` loop and `result_titles` in `research/writer.py` are Anthropic-specific. Each provider needs its own citation extraction.
8. **Batch**: `digest.py` and `monthly.py` use Anthropic batches. Both OpenAI and Google offer batch; each needs its own submit/poll/collect.

## Verify before building (could change the routing)

- Gemini (checked 2026-10-06 against the docs): Google Search grounding plus structured output in one request IS supported, but only on Gemini 3 series and marked Preview; the docs' example uses 3.1 Pro, so test 3.8 Flash itself. Gemini 3 bills per search query the model runs. No domain allowlist is documented, so treat it as absent: enforce `source_tiers` by rejecting citations from other outlets after the fact, or keep source-tiered jobs on sol. Citations come back as `url_citation` annotations (start/end index, URL, title), not Claude's result blocks.
- OpenAI (checked 2026-10-06): `gpt-6-luna` lists web_search, structured_outputs, prompt_caching and function calling; 1.05M context. The web search tool supports `allowed_domains` (up to 100), so source tiers can be kept on sol and luna. Still unconfirmed: web search and structured output together in one request (the search docs are silent), so test it. Luna is a reasoning model (effort none to max), so search content tokens are billed at model rates; set effort low for luna jobs. In Chat Completions, luna function calling only works with reasoning effort none; use the Responses API.
- Gemini 3.x prices double on 2027-01-01: re-cost flash then; at the doubled price it is still below sol on input but the gap to Sonnet narrows.
- Data retention and training-use terms of each API for portfolio data.

## Order of work

1. Verification questions above (read docs, one small test call each).
2. Provider interface plus Anthropic adapter, no behaviour change. Tests stay green.
3. Budget and price-table changes.
4. Luna: news classification only. Gate: accuracy on `tests/news/news_classification.json` within an agreed margin of Haiku. Then corroboration, calendar, verification, chat.
5. Flash: digests, then approvals, locate, ir_discovery, gap_research, research quick.
6. Sol: earnings extraction (gate: golden cases in `tests/golden/earnings/`), escalation, monthly review, research standard (gate: side-by-side on 3 to 5 holdings you know).
7. Keep Claude as the fallback per job until each gate passes; flip one job at a time in config.

## Caching

Today only Anthropic's explicit `cache_control` is used: chat history (`chat/assistant.py`), earnings system prompt (`earnings/extract.py`), corroboration (`news/corroborate.py`), research material and system prompt (`research/writer.py`). The other providers need their own handling, and there is a second layer worth adding.

### 1. Provider prefix caching

| Provider | How it works | Cached price | Verify |
|---|---|---|---|
| OpenAI (sol, luna) | Automatic on a repeated prompt prefix; no code needed, no write premium. Send a stable `prompt_cache_key` per job type so related requests land on the same cache. | sol $0.10 (5% of input), luna $0.01 (10%) | Minimum prefix length, cache lifetime, whether luna caches |
| Google (flash) | Implicit caching is automatic on repeated prefixes; explicit caches give a guaranteed discount but may carry a storage fee per hour. | $0.075 (10% of input) | Minimum tokens, explicit cache fee and lifetime, whether implicit caching reports hits in usage |

Rules for every call site:
- Static content first: system prompt, tool list, shared material. Per-request content last. The research writer and extractor already follow this; keep it in the new interface.
- No timestamps, request ids or varying order inside the cached prefix. "Today is ..." goes after the shared material, as `section_prompt` already does.
- Keep one model per job. A cache belongs to one model, so a fallback or downgrade mid-run loses it.

Where it pays most, in order:
1. Research (sol): up to 10 sections per run re-send the same company material.
2. Chat (luna): the whole history is re-sent every round, up to 30 rounds.
3. Earnings extraction (sol): the long system prompt and the report repeat across retries.
4. News classification (luna): the instructions repeat across 50-item calls.

### 2. Accounting (easy to get wrong)

- Anthropic reports cache reads separately from `input_tokens`. OpenAI reports cached tokens as a subset of the prompt tokens. Map them differently in `Usage.add`, or cached tokens get billed twice.
- `BudgetConfig` has one global `cache_write_multiplier` and `cache_read_multiplier`. Make both per model (OpenAI write 1.0, Gemini write 1.0 plus any explicit-cache storage, Anthropic 1.25 and 0.1).
- Record cache hit rate per job type next to cost so a silent drop shows up in `/config/budget`.
- Check that batch and caching discounts stack on each provider before assuming both.

### 3. Application-level caching (skip the call entirely)

Provider caching only discounts input tokens. Skipping a call whose answer cannot have changed saves all of it:
- News classification and corroboration: key on item content hash, prompt version and model; reuse the stored label.
- `ai_approvals`, `ir_discovery`, `gap_research`: do not re-judge a suggestion whose evidence is unchanged.
- Web search results: short TTL cache per query and allowed domains, so a re-run of research does not pay for the same searches.
- Research and review reports: reuse a section when its input material hash is unchanged since the last run.
Needs a small table (`ai_result_cache`: key, job type, model, prompt version, result, created_at) and a rule that a prompt or model change invalidates it.

### Order

Add caching after the provider interface (step 2) and budget changes (step 3), then per tier as each job migrates: turn on prefix caching, measure hit rate for a week, then add result caching for news and approvals first.

## Reducing cost

Ordered by saving versus effort. Judge each by cost per finished job from `job_step`, not per request: a cheaper call that needs retries is not cheaper. Items 1 to 4 need no migration and can be done now.

1. **Turn on the limits that already exist.** `BudgetConfig` has `monthly_cap_usd`, `per_job_cap_usd` and `default_job_cap_usd`, all null by default, so nothing is capped. Set a monthly cap, `at_cap: downgrade_noncritical` with luna as `downgrade_model`, and a per-run cap on `research`, `monthly_review` and `chat`. Keep `warn_at_fraction` at 0.8 for the early alert.
2. **Set effort explicitly.** Most jobs leave `effort` unset, so the model default (often high) applies. Use low on luna jobs, medium on flash jobs, medium on sol for escalation and chat-like work, and high only for earnings extraction, monthly review and research standard. On luna, reasoning tokens are billed as output, so low effort matters most there.
3. **Batch everything that can wait (50% off on all three providers).** Digests and review already do. Add `ai_approvals`, `ir_discovery`, `gap_research`, `earnings_calendar` and overnight news classification. Keep escalation, chat and earnings checks synchronous.
4. **Cap web search, the cost model changes do not touch.** Search is $10 to $14 per 1,000 calls on top of tokens. Research allows 40 searches and 60 steps per run, corroboration 10 per call. Lower those caps, use free data sources first and search only for gaps, and cache search results for a few days. Send search-heavy jobs to OpenAI ($10) rather than Gemini ($14).
5. **Shrink what is sent.** Send the relevant filing sections, not whole filings; keep `max_content_tokens` on fetches; summarise older chat turns instead of resending them and lower chat `max_steps` from 30; send a company's research material once per run.
6. **Shrink what is returned.** Lower `max_tokens` (16000 per research section is far above need), use "standard" depth (6 sentences) instead of "deep" (12) unless a holding needs it, and keep structured fields short.
7. **Do less work.** Skip news items already seen; add a keyword or source filter before classification; skip IR discovery and gap research for holdings that have not changed; reduce schedule frequency for jobs whose inputs change rarely.
8. **Cascade instead of one model.** Run luna first and escalate to flash or sol only when the answer has low confidence (classification, approvals, corroboration). Measure first: the larger model at lower effort sometimes beats a cascade because it keeps one cache.
9. **Result and prefix caching** (section above): news, approvals, search results, research sections.
10. **Watch the numbers.** Track cost per job type per week, cache hit rate, parse-failure and retry rates, and spend per provider; set spend limits in each provider's console as a second backstop.
11. **Re-price in January 2027.** Gemini 3.x prices roughly double on 2027-01-01; recheck whether flash is still the right middle tier or whether luna (with more care) or sol is cheaper per finished job.

Expected order of impact (verify with real spend from `/config/budget` before committing): search volume, research and earnings runs on sol, chat history, then news volume.

## Do it in code, not with AI

Rule: if a result can be computed or checked reliably by code, code decides and AI is only the fallback for what the code cannot settle. This is cheaper, repeatable and auditable, and it needs no provider. Several pieces already work this way (thesis verdicts, `numeric_check` in claim verification, insider-trade classification, 8-K deal extraction, the `data_context` report sentences, and the new `exposure_build` job, which builds revenue-by-country exposure from SEC XBRL with no model); extend the same pattern. Reuse `research/polite.py` for any new page probing, so it fetches politely.

| Job | Do in code | Leave to AI |
|---|---|---|
| News classification | Event type and severity from rule tables: EDGAR form type and 8-K item number, central-bank and regulator feed (ECB, Fed, CNB press), Form 4 via the existing insider classifier. Ticker linking by alias match. Duplicate and near-duplicate removal by URL, title hash and similarity. | Only items no rule claims (luna) |
| Earnings calendar | Dates from structured sources first (EDGAR 8-K Item 2.02, vendor calendars you already pay for, exchange calendars); keep `date_in_text` as the check on any AI-read date. | Reading IR pages when no structured date exists |
| Earnings extraction | Figures from XBRL or `sec_xbrl`/FSDS data for US filers, ESEF for EU filers; verdicts stay code. | Reports with no structured data (non-US, PDF only) |
| Locating the release | EDGAR submissions API for the 8-K and its exhibit; IR RSS feeds; sitemaps and known URL patterns. | Web search only when all of those fail |
| AI approvals, listings | Exact match on ISIN, or ticker plus exchange (MIC) plus currency, auto-approves; clear mismatches auto-reject. | Ambiguous matches |
| AI approvals, peers | Same industry code (SIC or similar), market-cap band, overlap with vendor peer lists. | Conflicts between those signals |
| IR page discovery | Probe common paths (`/investors`, `/investor-relations`, `ir.<domain>`), robots and sitemap, HTTP status and title keywords. | Choosing between several candidate pages |
| Corroboration | Match the same story across feeds by entity, time window and title similarity before any web search. | Stories that appear in one source only |
| Claim verification | Extend `numeric_check` with entity, date and period matching and quote matching against the cited snippet. | Sentences code cannot settle |
| Research report | Sections built from stored data by code: financial trend, valuation (computed from the valuation methods), ownership, liquidity, relationships, risk figures. Roughly half of the ten sections. | Business, moat and risks, strategy fit, open questions |
| Digests | Template the low-severity items (headline, ticker, source, link); the tuned `min_severity_summarised` already limits summaries. | Narrative for items at or above the severity threshold |
| Chat | Questions about positions, prices, exposure, dates and cost answered from read models and templates. | Synthesis, explanation, comparison, anything open-ended |
| Gap research | Skip when a free source or earlier run has the fact (see free-source jobs); skip when inputs are unchanged since the last attempt. The new `annual_revenue` and segment slots overlap with SEC XBRL data: take them from `sec_xbrl`/`exposure_build` first and search the web only for holdings with no filing data. Keep the existing code checks (`check_segments`, quote must print every number) as the gate on anything AI returns. | Genuinely missing facts |
| Web domain tier | List first (config lists, deny list, issuer's own domain, owner decisions, `web_domain_tier`); a TLD rule for government and regulator domains. | Unlisted publishers only |

How to do it safely:
1. Build the rule next to the current AI path, not instead of it. Run both on the labelled and golden sets (`tests/news/news_classification.json`, `tests/golden/earnings/`) and compare.
2. Replace the AI step only for the slice where the rule agrees with the current labels at an agreed rate and is wrong in no costly direction (a missed severe event is worse than a wasted call).
3. Store who decided (`decided_by: rule` or `model`) and the rule id on each result, so a wrong rule is found and fixed, and the share still reaching AI is visible. The new AI decisions page (`/ai-decisions`, `GET /approvals/ai-summary`) already lists approver decisions for listings, peers, pages and facts; extend it to show the deciding rule or model.
4. Send to AI only on explicit "cannot settle" outcomes (no rule matched, signals conflict, source missing), never because code raised an error.
5. Re-measure cost per job type after each replacement; the saving is the AI calls that no longer happen, so count them.

Order by saving and risk: news classification rules and dedupe, earnings figures from XBRL, locating the release, listing and peer approvals, research sections built from data, chat read-model answers, then IR discovery and corroboration.

## Gates

- Earnings: golden cases pass with no new wrong figures.
- News classification: accuracy on the labelled set.
- Research: sections are factually correct, cite only allowed tiers, parse into `SectionDraft`.
- Cost: compare per-run cost from `job_step` before and after, not per-token price.
