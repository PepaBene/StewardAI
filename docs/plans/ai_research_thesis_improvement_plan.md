# AI research and thesis creation improvement plan

**Status:** research-backed proposal  
**Date:** 2026-09-30 (status refreshed 2026-10-10)  
**Scope:** Deep research, thesis creation/review, research chat, and configurable AI analysis in StewardAI

## Executive recommendation

Build one **grounded research workflow** over StewardAI’s existing domain services, rather than a separate chatbot with a large prompt stuffed with database rows. The workflow should plan the question, retrieve only relevant private portfolio data through narrow read-only tools, gather new web evidence iteratively, compute financial figures in code, and write an answer into a validated user-selected structure. Keep evidence, data freshness, strategy/config versions, and uncertainty attached to every conclusion.

The repository already documents substantial foundations: research and chat are queued worker jobs; research profiles configure sections, depth, model, search/step budgets, allowed source tiers, peers and output language; the research report separates code-written and model-written sections; valuation figures are computed in code; citations resolve to stored sources; and the worker checkpoints AI steps. The backend has dedicated `steward.research` and `steward.chat` packages, `research_routes.py` and `chat_routes.py`, configurable research profiles and prompts, as well as an existing report and thesis UI. The technical product description also calls for strategy-aware research, reports on non-held tickers, chat that can answer from database facts, and claim-level citations.

The main improvement is to make these capabilities comprehensive and reliable across **all relevant backend data**, general research questions, and user-defined output formats. Avoid passing the whole database to the model. Expose a versioned catalog of typed, bounded, auditable read tools, then build an evidence-linked report that can be reproduced and checked.

## Status after the disclosure, Morningstar, quant and news work (verified in code, 2026-10-10)

The four data plans are implemented, which changes the starting point of every phase below. The phases keep their scope; this table says what is already in place and what is left.

| Phase | State | What exists / what remains |
|---|---|---|
| 0 Baseline | Done for chat (2026-10-11) | Exists: claim-verifier eval set (`verify/eval.py`, `evals_gate.py`), news golden sets, and now the [capability map](ai_research_capability_map.md) generated from `chat/catalog.py` and tested against the real tool list; 41 labelled research/chat cases in `evals/research_cases.json` over ten categories with a trace scorer (`chat/benchmark.py`); a per-call trace (tool, arguments, ms, ok/error, sources added, bytes) on every chat answer step. Remains: a research-job trace of the same kind (web queries and URLs arrive with Phase 2), user feedback linked to a run, and a live runner that replays the cases against a model and records human review scores. |
| 1 Typed data access | Done for chat (2026-10-11) | Exists: `data_context/` read models with `known_as_of` cutoffs; research `gather` sections from stored records; chat tools grew from 27 to 37 with `filing_evidence` (filed notes, 13D/G, 13F, N-PORT, insider, dependencies, deals, consensus), `fundamentals`, `valuation_scenarios`, `price_history`, `macro_series`, `strategy_rules`, `transactions` (paged), `risk_history`, `thesis_and_reports` and `data_freshness`; the shared figure envelope (`chat/envelope.py`: status ok/missing/not_applicable/stale/failed, unit, currency, period, `as_of`, `source_id`, reason) used by all of them; every result stamped with `tool` and `retrieved_at`; all lists capped with a stated `page`. Remains: move the earlier analytics tools' figures into the envelope, FX and intraday quote tools, reading saved valuation scenarios, and giving the research job the same tools (the research report still reads through `gather`). |
| 2 Planning and web quality | Done in the backend (2026-10-11) | Exists: a plan schema built in code per run (`research/plan.py`: questions, stored-data tools, web intents, tiers, counter-evidence search, stop rule, planned sections), six research modes plus profile controls (horizon, web emphasis, allow and deny domains, forums off by default, iterate, rounds, minimum sentences), a source policy that labels each web source primary, quality news, secondary, owner-trusted or forum (`research/source_policy.py`), follow-up rounds that search again for thin sections and ask for a second source for single-source claims, a deliberate `counter_case` section, every URL the search returned kept with its status and limits, and the search provider read through one adapter (`research/web.py`). Remains: see "Gaps left after Phases 2 and 3". |
| 3 Output and thesis authoring | Done in the backend (2026-10-11) | Exists: report templates kept apart from profiles (sections, order, titles, detail-only, owner-defined sections), a versioned template schema, concise and detailed views over the one stored report with a Markdown export, four authoring modes, thesis proposals as field-by-field diffs with the evidence behind each line, an accept route that saves a new `theses` version only if nothing changed since the report, and a tamper-evident snapshot of profile, template, prompts, versions and thesis in every report. Remains: see "Gaps left after Phases 2 and 3". |
| 4 Claim grounding | Done in the backend (2026-10-11) | Exists: sentence-level source IDs, claim verifier over every model sentence, "insufficient evidence" path, code-owned numbers, and now a claim ledger stored in every research report (`research/ledger.py`): each sentence a typed claim (fact, calculation, interpretation, strategy assessment, forecast, unknown) with its evidence (source, kind, tier, role, date), a confidence label rated from the evidence, a period, a stale flag and a review state; a deterministic verifier pass (cited ids exist, figures and dates are in the cited sources, valuation figures name formula and inputs, planned sections not empty, strategy section names configured rules, thesis KPIs named with their stored verdict and the config version, balance of support, challenge, unknowns and scenarios, stale and undated sources); a report-level confidence with its reason; as-of date, data interval and notices. Remains: see "Gaps left after Phases 4 and 5". |
| 5 Security and eval | Done in the backend (2026-10-11) | Exists: read-only tool design (now tested: chat tools write only a `chat_action` proposal; research code writes only the report and its working records), owner-confirmed `propose_job`, budget guard and routing, redaction, and now: instruction-like text in a source is flagged, replaced in the prompt and rated low confidence; links and images in model text that are not report sources are removed; owner routes to delete a run's working records (URL list, web snippets, checkpoints, tool traces) and to sweep old ones; owner confirm/reject of a claim; an offline regression gate (`research/gate.py`, `evals/research_regression.json`, `evals/research_gate.json`) over injection, outbound links and claim typing, plus a fingerprint of prompts, tasks, tools and default templates; SSRF refusals tested. Remains: see "Gaps left after Phases 4 and 5". |

Cross-plan notes:

- Retrieval over prose is covered by the [agentic and RAG plan](agentic_workflows_and_rag_plan.md) (AG2), not here. News already has multilingual embeddings and semantic search; filings, reports and theses do not.
- Research profiles and prompts are model-routed through `llm/routing`; keep the "adapter so the provider can change" rule in Phase 2 pointed at that layer.
- News-derived thesis links, impact chains and framing-conflict flags exist in the news engine; Phase 4's "disconfirming evidence" step should consume them rather than re-search.

## Gaps left after Phases 0 and 1 (2026-10-11)

Phases 0 and 1 are built for chat only. Everything below is still open; later phases and exit criteria depend on it.

**Phase 0 (baseline)**

1. The research report job has no per-call trace like chat's. Its steps record usage and checkpoints, not retrieval records, web queries and URLs, retries or output validation.
2. No live benchmark runner. `chat/benchmark.py` scores a trace but nothing replays the 41 cases against a model, stores scores per run, or compares runs. The labels name expected tool groups, datasets and traps, not expected internal fact values, because those need seeded data.
3. Only the retrieval failure stage is scored by code. Analysis, citation, strategy and rendering failures, fact and numeric accuracy, citation support, strategy traceability and balance are scored by hand with no review tool or stored scores.
4. User feedback is not linked to a run. Cost and latency by step exist as usage in `job_step`, but there is no per-profile or per-case view.
5. Redaction and retention of traces are not decided. The chat trace keeps clipped arguments and result sizes, not bodies; there is no deletion control (Phase 5).
6. The benchmark does not change with holdings and data, and has no cases built from real failures yet.
7. The decisions the plan wanted captured in Phase 0 are still open: custom formats as sections or schemas, export formats, full-portfolio default, languages and regions, and quick versus deep cost budgets.

**Phase 1 (data access)**

1. The 27 earlier chat tools (stress test, risk forecast, factor tilts, theme exposure, event paths and the rest) are stamped with `tool` and `retrieved_at` and register sources, but their figures are not in the envelope; missing, stale and zero are not told apart inside them.
2. ~~The research report job does not use the new tools.~~ Partly closed (2026-10-11): a `read_tools` step (`research/read_tools.py`) calls the tools the plan names, through `ChatTools`, in code and without a model: bounded fixed arguments for the run's ticker, sources added to the run's registry, a compact record per call in `ResearchBody.tool_calls` (tool, arguments, status, ms, sources added, figures by status, newest as-of) and a report note when a tool is stale, missing or failed. The results go to the sections that planned them as extra evidence; `gather` stays as the code-written baseline. Still open: the model does not choose tools (the set is the plan's, fixed); tools that need an owner (`event_paths`, `theme_exposure`, `fund_overlap` and other owner-scoped ones), a geography or a series (`country_context`, `macro_series`) are recorded as not called, because a run has no owner and does not know them; `gather` and the tools still read the same tables by separate code, so figures can differ and nothing yet reconciles them; the comparison of research against chat is only data (records) with no scorer; the new step is covered by unit tests with fakes and one resume path that no DB test drives.
3. No tools for FX rates, intraday quotes, listing liquidity, raw statement lines, segment revenue, share counts, corporate actions, peers, ETF breakdowns, fund NAV and factsheets, patents, procurement awards, or the coverage and quality review queues.
4. `valuation_scenarios` uses default assumptions only and ignores the owner's saved scenarios. It builds ratios without peers, so peer comparisons are missing.
5. `fundamentals` returns the latest value per ratio, not the series. Multi-period trends need a further call or tool.
6. `filing_evidence` returns one section per call, cut to a few items with long passages shortened; there is no pagination or "more" call for it, `macro_series` or `risk_history`.
7. `risk_history` shows top-level numbers only, and cannot compare snapshots that have different method versions.
8. `price_history` covers daily closes only, thinned to 120 points, with no benchmark or total-return series.
9. The "prior reports" side of `thesis_and_reports` returns the last two research or review reports with 600-character excerpts and no structured sections. Digest and escalation reports are excluded.
10. ~~Freshness limits per dataset in `data_freshness` are fixed constants, not configuration;~~ Closed in part: limits are now `JobsConfig.chat.freshness_days` (defaults unchanged). Still open: it probes eight datasets and does not cover filings, 13F, N-PORT or consensus per ticker.
11. Source records for tool figures carry a snippet of the values, not the filing passage; ratio and valuation sources point at the company page rather than a stored record id.
12. No least-privilege check: tools run under the backend role, and scope is "owner" by convention (owner-only data is not filtered per user because the app has one owner).
13. Tool descriptions and the system-prompt addition are not evaluated: the chat component's eval fingerprint will change and `evals/baseline.json` needs a re-run with `--write-baseline` before the eval gate passes.

**Test coverage of the new code**

1. `tests/test_research_tools_db.py` covers strategy, transactions, risk history, macro, thesis and reports, evidence coverage, freshness and uncovered-ticker errors. The success paths of `fundamentals`, `valuation_scenarios` and `price_history` (a covered company with statements and prices) and of `filing_evidence` with real records are not tested, because they need a seeded filing and price fixture.
2. No test drives the model loop with the new tools, so tool selection by the model is unmeasured until the live runner exists.
3. The older tests that construct `ChatTools` or compare exact tool results were not re-run after results gained `tool` and `retrieved_at`.

**Later phases (unchanged, not started)**

Phase 4 typed claim ledger and strategy-rule status per claim; Phase 5 injected-article regression case, retention and deletion, and regression gates. See the table above.

## Gaps left after Phases 2 and 3 (2026-10-11)

Phases 2 and 3 are built in the backend only. The web app has no screen for the new profile fields, templates, view toggle, search coverage or thesis proposals; its API types were regenerated and nothing else was changed there.

**Phase 2 (planning and web quality)**

1. ~~The research job still reads stored data through `gather`; the run does not call the planned tools.~~ Partly closed (2026-10-11, see Phase 1 gap 2): the run now calls every planned tool it can (read-only catalog tools only, enforced in code and tested) in a checkpointed, cost-free `read_tools` step before the sections are written. Still open: the plan stays a fixed list, so the model neither picks nor repeats tools, and a section that finds a gap cannot ask for another read; `gather` is not removed.
2. The plan is built in code from the profile and layout. No model breaks the question down, the owner cannot edit the plan before the run, and the plan is shown nowhere but the stored report.
3. The routing client builds its search-result block from the pages the answer cited, so "found but not cited" holds only what the provider exposes. We cannot tell a page the model opened from a snippet it saw, and the provider gives no page age, so every URL carries the "undated" limit. `SearchHit.page_age` is read when a provider supplies it.
4. ~~A second source is asked for in the prompt. Code does not check that the second source is a different publisher.~~ Closed (2026-10-11): `single_source_claims` counts two pages of one host as one source, so such a claim is still sent for corroboration.
5. Thin means fewer cited sentences than the profile's minimum, not "a plan question went unanswered". At most three sections are followed up per round.
6. `date_horizon_days` is a prompt instruction. Code does not drop or flag sources by date, because search results carry none.
7. Owner-trusted domains have no tier. ~~The forum list is a fixed constant in `source_policy.py`, not configuration.~~ Closed: `ResearchProfile.forum_domains` extends the built-in forum list per profile (validated like `allow_domains`, no overlap with allowed or denied domains). The paywalled list is still a constant.
8. `portfolio_macro` mode is still one ticker with a macro and exposure lens; there is no portfolio-wide run.
9. The exit criterion (deep mode retrieves more unique, relevant evidence than one-shot search) is not measured. Each report records `sources_after_first_pass` and `sources_added_by_follow_up`, but comparing modes needs the live benchmark runner (Phase 0 gap 2).
10. The new section prompts (`counter_case`, `thesis_kpis`, `scenarios`, `catalysts`, `executive_summary`) and the source-order paragraph are not evaluated against the benchmark.

**Phase 3 (output and thesis authoring)**

1. Custom output is a section builder only (title and a plain-words question, at most six, no links or markup). A validated JSON Schema format is not built.
2. Export is Markdown. The server-side PDF of the company report is not wired to templates or views.
3. The snapshot is tamper-evident (a digest over its content), kept inside the report body. The database does not stop an edit to the report row.
4. Templates live in the `research_profiles` config document, not their own domain: a separate domain needs a migration of the `config_version` check. A template cannot set the language; the profile does.
5. A proposal covers `why_owned`, `exit_conditions` and `pre_mortem`. It takes the report's own checked sentences verbatim, so the owner edits prose written as report text. KPI thresholds, scenario prices, probabilities, variant perception, conviction and base rate stay with the owner.
6. The accept route applies whole fields. It keeps no record of fields the owner declined, and the report does not link to the version that accepted it (the version note names the report).
7. `review_thesis` lists each KPI against its latest stored verdict. It does not run the earnings check or write a model assessment.
8. The concise view uses fixed caps (3 model sentences, 4 code sentences per section).
9. Not tested: a live model run, the web screens, and the claim verifier on follow-up sentences with a real provider.

**Web app (report page built, profile and template editors open)**

Done on the report page (`apps/web/src/app/(app)/research/[id]/`, helpers and vitest tests in `src/lib/research/`): the concise/detailed toggle (uses `GET /research/{id}?view=concise`; feedback paths still point at the stored section positions), the search coverage panel with the follow-up round summary, the plan display and the stored-data tool call table, the run snapshot view with a purge button behind a confirmation step, source role and caveat notes with the low-confidence injection flag, and the thesis proposal diff with a per-field accept that shows the 409 "run the research again" answer as its own message. Only the helper unit tests ran. Type-check, lint, a render test of the components and a browser check were not run, and the accept, review and purge calls were not exercised against a running API. The concise view drops the discovered-URL list (the API removes it), so the coverage panel then shows counts only.

Of the list below, items 3, 4 and 5 are built; items 1, 2 and 6 are still open.

1. ~~No editor for the new profile fields.~~ Built (2026-10-11, not yet checked in a browser or by `tsc`/lint): `research-profiles-editor.tsx` and `research-profiles-form.ts` now edit mode, web emphasis, date horizon, trusted, blocked and forum domains, allow forums, stale-day overrides per source kind, iterate, rounds, minimum sentences, template and authoring mode. The form mirrors the backend checks (domain pattern and counts, allow/deny/forum overlaps, ranges, unknown template) and shows errors under each field. Only the form logic is unit-tested; the screen was not rendered or opened in a browser.
2. ~~No template editor.~~ Built (same status): `report-template-card.tsx` edits each template (id, name, first view; per section enable, order, title override, sentence cap, detailed-view only, add and remove; up to six owner-defined sections with a title and a plain-words question). A template cannot be removed while a profile uses it, and renaming a template id re-points the profiles that use it (not when the id is cleared first). Limit: the owner-defined section id is generated (`custom-section`, `custom-section-2`, ...) and cannot be edited.
3. The report page has no concise/detailed toggle, no search coverage panel (found, cited, excluded URLs and their limits), no plan display, no follow-up round summary and no snapshot view.
4. No screen for a thesis proposal: no diff view, no per-field accept, no handling of the 409 "run the research again" answer. The accept route exists only in the API.
5. The new source roles (owner-trusted, forum) and their caveats are not shown on source cards.
6. ~~Nothing tests that the web defaults match the backend.~~ Partly closed: `research-profiles-form.test.ts` asserts the ids, section lists, per-profile defaults of the new fields, detail-only sections and template/profile section agreement, and (when `apps/backend` is present) reads `research.py` as text to compare the section list, the custom/template section limits and the stale kinds. There is no committed JSON export of the backend defaults (they are pydantic default factories; `openapi.json` holds no values), so the profile and template defaults themselves are still compared against values transcribed by hand into the test, not derived from Python. A backend default change fails the test only if it also changes one of the text-checked items or the gate fingerprint.

**Verification and process**

1. The full backend suite has not been run on this change. The run was killed for low memory (Windows temp-folder permission error first). Run on their own and passing: the new unit and DB tests, one older research resume test, the API contract, writer, config and response-model tests. Other older tests that touch research profiles, reports or the writer (the rest of `test_phase5.py`, company report tests, and any that assert the default profile list) are not re-run.
2. DB tests must run one process at a time: two processes against the same test Postgres collide on the shared role and fail with password or "tuple concurrently updated" errors.
3. mypy reports four errors in `tests/test_phase1b.py` and `tests/test_phase5.py` that predate this work and were not fixed.
4. The chat eval baseline (`evals/baseline.json`) and the earlier ChatTools tests are still outstanding from Phase 1; the new research prompts add to the same unevaluated surface.
5. Existing stored research reports have none of the new fields. They load with defaults (no plan, no snapshot, no discovered URLs), so the new views and checks show nothing for them.
6. Test fakes pass text through the code-side claim check, which removes a sentence containing a number no source has. Tests must keep fake sentences free of digits; this is easy to trip over.
7. Nothing is committed or pushed yet.

## Closed after the fifth pass (2026-10-11)

Built, with what was and was not run. A DB-backed test was run only against a local Postgres 17 without the `pgmq` extension, so anything that enqueues a job skipped.

- **Phase 0 gap 1, research-job trace.** `research/trace.py` and `ResearchBody.trace` record per step: model calls (tokens, cost, ms, outcome), web queries and URLs found and cited, sources offered against cited per section, and validation drops with reasons. Metadata only, no bodies. `purge_old_traces` and `scrub_body` also remove it. Not visible: retries inside the HTTP client, and which pages the model opened (cited pages stand in). A trace panel is on the report page.
- **Phase 0 gaps 2, 3, 6.** `chat/benchmark_runner.py` replays the cases through the chat assistant with an injected client, stores a run as JSON under `evals/runs/` (git-ignored), compares two runs, merges hand-scored review files (analysis, citation, strategy, rendering) and appends real failures to `evals/cases_extra.json`. Cost is only recorded when per-token prices are passed. The CLI `run` path was never run (needs a database and a provider key). Eight cases were added (49), so every new tool is reached by a case. Still open: gap 4 (feedback linked to a run), gap 7 (decisions) and a per-profile cost view.
- **Phase 1 gap 3.** Eleven chat tools: `fx_rates`, `latest_quote`, `listing_liquidity`, `statement_lines`, `share_counts`, `segment_revenue`, `corporate_actions`, `peers`, `etf_breakdown`, `fund_nav`, `coverage_review`. `fundamentals` takes `periods` (gap 5) and `price_history` takes a `benchmark` (gap 8). Not built, no data: patents, procurement awards. `tests/test_data_tools_db.py` runs every query against the migrated schema on empty tables and checks one seeded FX row; the success paths of the other tools are not tested with data. The research run does not call the new tools yet (`read_tools.py` is unchanged).
- **Phase 2 gap 2, model-written plan.** `research/model_plan.py`: a cheap model call with schema-checked output, fixed source groups (the model names no tool or domain), fallback to the code plan with a recorded reason. Routes `POST/GET/PATCH /research/plans` and `POST /research/plans/{id}/start`; the stored plan carries `authored_by`, `edited_by_owner`, `draft_id`. Migration `20261105100000_research_plan_draft.sql` adds the draft table. Profile fields `planning` (default `code`) and `planner_model`. The web plan editor sits in the research list view. The planner's cost is stored on the draft, not as a job record. The start path through the queue was not run (no `pgmq` locally). The profile editor does not expose `planning` yet (it passes through unchanged).
- **Phase 4 gap 4, rule evaluation.** `research/rules.py` runs the portfolio rule engine against the current portfolio overview and gives each rule that applies a status (pass, breach, warn within 10 % of the limit, unknown with a reason such as "not held"), the measured value, limit and as-of date, in `StrategyCheck.compliance`. The ledger panel shows it apart from the report's own reading. Tested with fakes; not against a seeded portfolio.
- Not done in this pass: a browser check of any screen, the full backend suite, and the chat eval baseline.

## Current architecture and product intent

From the repository’s technical description and code layout:

- Python FastAPI receives chat and research actions; AI work runs in a persistent worker through a database queue. Research steps are checkpointed to avoid repeating billed work after interruption.
- Research is already designed as configurable profiles and sections. Profiles choose depth, models, source tiers, peers, language, and budgets.
- Research draws on current company/fundamentals/valuation data, filings, news, market/portfolio information and the web. Code is intended to calculate numbers; model prose explains them.
- Existing reports and UI handle structured research sections, valuation, sentence-level source IDs and source cards. The app also stores strategy, per-holding theses/KPIs, prompts and configuration history.
- Chat is intended to query backend data, search/fetch the web, answer with sources, and start a job when the question merits a longer report.

Relevant implementation areas include `apps/backend/src/steward/research/`, `apps/backend/src/steward/chat/`, `apps/backend/src/steward/config/research.py`, `apps/backend/src/api/research_routes.py`, `apps/backend/src/api/chat_routes.py`, and `apps/web/src/lib/research/report.ts`. Since this section was written, `steward.data_context`, `steward.thesis`, `steward.verify`, `steward.decision`, `steward.news` (analysis engine, wire, embeddings) and `steward.events` have been added or grown; see the status table above. During implementation, inventory actual read tools, source records, schemas and profile validation against this plan before changing them; this plan does not assume every capability described in the product document is fully implemented in the current runtime.

## Gaps left after Phases 4 and 5 (2026-10-11)

Phases 4 and 5 are built in the backend for the research job. The chat answer path got the injection wording it already had and the write-path test, but not the ledger. The web app shows none of it.

**Phase 4 (claim grounding)**

1. Claim types are assigned by code from the section and the sentence's wording (section map, hedge words, future words, "not disclosed"). They are a good first sort, not a judgment: a hedged fact reads as an interpretation, an unhedged opinion as a fact. The model does not label its own claims, and no human has measured the labels.
2. Confidence is rule-based: stored record or primary source plus a passed claim check is high; one secondary web source is medium; unchecked, unsupported, flagged or uncited is low; judgments stop at medium. The thresholds (70 % high for a high report, 30 % low for a low one) are first guesses with no calibration against reviewed reports.
3. "Figures agree with code outputs" is checked as "the numbers and dates in a claim are in the text of the sources it cites" (the existing number matcher). It does not recompute a valuation, and a derived figure (a growth rate computed from two stored values) can warn although it is right. Those are warnings, not errors.
4. Strategy rules are named, not evaluated: a rule's status is always `unknown` in the ledger because the formal check lives in the portfolio rule engine (`portfolio/breaches.py`) and needs a drift report. Only thesis KPIs carry a formal status, taken from the stored verdicts through `review_thesis`. Qualitative fit and formal compliance are kept apart, but compliance is not filled in for rules.
5. Disagreement between sources is not detected. The balance check asks for a counter-case, open questions and scenarios when the template plans them; it does not compare two sources that contradict each other (the news engine's framing-conflict flags are not consumed).
6. The ledger is built after the claim verifier and does not feed back: a failed check does not trigger a rewrite or a regeneration, it lowers the label and lists the problem.
7. ~~Stale limits are fixed constants per source kind~~ Closed in `build_ledger` (`stale_days` argument) and `ResearchProfile.stale_days` (overrides on the built-ins: 7 days for a price, 90 for news and observations, 180 for a search result, 550 otherwise). `run.py` must pass `stale_days=profile.stale_days` to `build_ledger` for a profile setting to take effect.
8. The owner can confirm or reject a claim by route; rejecting does not remove the sentence or flow into the thesis proposal, the benchmark or a correction list. The report row is edited in place and the snapshot digest does not cover the ledger.
9. The chat answer path has no ledger. Chat sentences keep source ids and the claim check; they are not typed or rated.
10. Existing stored reports have no ledger and load without one.
11. The news engine's own claim checks (`news/claims.py`) were not moved onto the shared ledger format.

**Phase 5 (security, evaluation, improvement)**

1. Injection detection is a pattern list over English text. A paraphrase, another language or an instruction split across sentences passes it. It reduces the risk and marks the source; the real limit is that the model has no write, fetch-by-address or configuration tool, which `tests/test_research_security.py` pins down.
2. ~~Chat has no injection or outbound-link defence.~~ Partly closed (2026-10-11): the chat answer now flags sources with instruction-like text and strips links and images that are not its own sources. Tool results now have instruction-like sentences replaced by a marker before the model reads them (`neutralise_tree` in `chat/envelope.py`, applied in `ChatTools.call`). The detector is still the English pattern list from gap 1.
3. `strip_outbound` removes links and images that are not report sources. It does not stop the model naming private data in plain words; that would also be visible to the owner in the report.
4. Web search is done by the provider through the routing client. What query text the provider sends out is not inspected for portfolio details (the section prompt carries the company name and the plan's search intents, not holdings, but nothing tests this).
5. ~~Retention is by owner request and by a sweep route; nothing calls the sweep on a schedule, and there is no setting for a default retention period.~~ Closed: the daily job `research_trace_retention` (03:40, enabled) calls `purge_old_traces` with `JobsConfig.research.trace_retention_days` (default 30). Still true: the sweep removes checkpoints and chat tool traces of succeeded jobs; it does not remove `job_step.detail` for other steps, report bodies, or `web_fact` rows. Deleting checkpoints means a finished job cannot be resumed (it does not need to be).
6. Purging a report's working records edits the stored body; the stored Markdown (`body_md`) is not rewritten, though it holds no URL list and no snippets.
7. The regression gate is offline: it scores injection detection, outbound removal and claim typing on 26 fixed cases, and fingerprints prompts, section tasks, plan questions, tool catalog (service and bounds), default profiles and templates, the benchmark cases and the code of the ledger, untrusted-text and source-policy modules. It does not run a model, so it cannot tell whether a changed prompt made answers worse; it only forces a recorded, reviewed rewrite of the baseline. The live eval gate (`verify/evals_gate.py`) still covers earnings, classification and claims only.
8. The benchmark suites of Phase 0 (41 cases) still have no live runner, so the metrics the plan lists (recall, numeric and citation accuracy, strategy alignment, counter-argument coverage, unknown calibration, cost, time, owner correction rate) are not produced. The owner correction rate has a data source now (claim review state) but no report.
9. The eval set does not change with holdings and data, and has no case for entity ambiguity, conflicting sources or strategy changes in the offline suite. Real failures are not yet fed in; there is no screen or route for failed high-impact reports.
10. SSRF and robots handling for fetched pages were already in `documents/fetch.py` and `research/polite.py`; this work added tests for the refusals (loopback, private, metadata, non-http schemes) with a fixed resolver. DNS rebinding after the check and redirects are covered only as far as that module covers them.
11. `evals/baseline.json` for the claims, classification and earnings components was not re-run (no key in this environment); the chat eval fingerprint from Phase 1 is still outstanding.

**Web app**

The report page now shows the claim ledger (`claim-ledger.tsx`, `ledger-claim-row.tsx`, helpers in `src/lib/research/ledger.ts`): report-level confidence with its reason, claim type and confidence label with reason per claim, the stale flag, the review state with confirm and reject buttons (calling `POST /research/{id}/claims/{claim_id}/review`; pressing the active choice again clears it), the as-of and coverage notices, strategy checks, the balance result and flagged-source warnings. The purge control is in the run snapshot panel. Reports stored before the ledger existed show none of this and render without error (every field is optional). Not run: type-check, lint, a render test of the components or a browser check; the review and purge calls were not exercised against a running API.

**Verification and process**

1. The full backend suite has still not been run (the earlier attempt was killed for low memory). Run on their own and passing: the new ledger, security and gate unit tests, the new Phase 4–5 DB tests, the Phase 2–3 DB and plan tests, and the research resume test. No other older test that touches research reports or routes was re-run after `ResearchBody` gained fields and `research_routes.py` gained routes.
2. No test file for the OpenAPI export was found, so the regenerated `openapi.json` and `schema.d.ts` are checked only by `tsc` and by the routes importing cleanly.
3. The web suite passes except `scripts/check-colors.test.ts`, which timed out at 5 seconds scanning `src/`. It was not re-run with a longer limit, so whether it is flaky or slow on this machine is not known.
4. `evals/research_gate.json` is a fingerprint of prompts, tasks, tools, default templates and the ledger, untrusted-text and source-policy code. Any edit to those files fails `test_research_gate.py` until `python -m steward.research.gate --write-baseline` is run. This is intended, but it is not yet wired into CI beside `evals_gate`.
5. The live eval baseline (`evals/baseline.json`) was not re-run: no provider key here.
6. The new DB tests take several minutes (about 3 minutes for the Phase 4–5 file) because every DB test migrates a fresh database; they must still run one process at a time.
7. Fake sentences in tests must keep avoiding digits (the code-side claim check), and ledger tests that use digits need the number in the source snippet.
8. Four mypy errors in `tests/test_phase1b.py` and `tests/test_phase5.py` predate this work and remain.
9. A recursive search over `src` was killed for low memory during this work; later searches were scoped to single packages.

## Research findings

### What a useful research agent does

Deep research is an iterative process: decompose the user’s question, retrieve internal and external evidence, assess gaps, issue follow-up queries, reconcile conflicts, then synthesize and cite. OpenAI’s API documentation describes a similar agentic loop and supports combinations of web search, internal file search/MCP sources, and code execution; it emphasizes that private data needs explicit tool/source setup, not just a prompt. [Deep research guide](https://developers.openai.com/api/docs/guides/deep-research)

For StewardAI, internal sources should remain first-class tools owned by the backend. The model should request the kinds of facts it needs (e.g. filings, reported fundamentals, recent company news, valuation inputs, portfolio exposure, thesis KPIs) rather than receiving a raw database dump. Use deterministic code for financial calculations and structured model output for the narrative/report object. Structured Outputs can constrain generated responses to a supplied JSON Schema; function calling is intended for connecting a model to application tools, while a response schema shapes the final answer. [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)

Web search tools can return citations and source metadata, and some support iterative open/find actions and domain filters. Citations need to be rendered as clearly visible links in the end-user UI; keep the source inventory as well as the subset cited in prose. [Web search guide](https://developers.openai.com/api/docs/guides/tools-web-search)

### Main failure modes to design against

- **Context overload:** a long context containing every record is expensive and makes relevant facts harder to distinguish. Retrieve by entity, time range, topic, and task-specific source domains.
- **False precision or stale facts:** the model may use old data, mix currencies/periods, or turn missing data into zeros. Make units, period, as-of time, source, restatement status, and missingness explicit; compute totals and ratios in code.
- **Citation theater:** a link at paragraph end may not support the actual claim. Store a claim/evidence ledger and verify each material claim against one or more source passages or typed database facts.
- **Strategy drift:** a generic “good company” argument does not answer whether it fits this user’s own strategy. Evaluate explicit strategy rules and holding thesis conditions separately from analyst interpretation; show the exact rule/KPI and its version.
- **Confirmation bias and one-sided search:** formulate a base case, strongest counter-case, disconfirming evidence, and unresolved questions. Search for the counter-case deliberately.
- **Untrusted retrieved instructions:** web pages, filings, stored news and user documents may contain instructions aimed at the model. Treat retrieved content as evidence, never as authority to change tools, policy, or access. OWASP’s guidance covers prompt injection through retrieved content and RAG. [OWASP Prompt Injection Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- **Unmeasured quality:** fluent reports can conceal unsupported claims, missed facts, or bad strategy matching. Use a versioned, human-reviewed benchmark and trace retrieval, citations, outputs and corrections. [OpenAI evals overview](https://developers.openai.com/learn/evals)

## Target research flow

1. **Intake and scope.** Classify request as quick answer, holding research, thesis create/update, portfolio-wide analysis, or open-ended research. Resolve entities and ask for missing scope only when it changes the result. Select a saved profile/template or build a temporary one from user options.
2. **Build a research brief.** Convert the request into questions and subquestions, time horizon, entities, required strategy lenses, freshness requirements, internal source groups, web domains/source tiers, and output schema. Keep the brief visible/editable for long jobs.
3. **Retrieve private data via domain tools.** Use narrow read-only backend functions for portfolio, transactions/exposure, holdings and fund look-through, prices/market history, fundamentals and valuation inputs, filings/earnings/KPIs, news/events, macro/FX, existing theses, strategy rules, prior reports, and data freshness/quality. Respect the user's scope; a single ticker request should not include unrelated holdings unless portfolio context is relevant and requested/profiled.
4. **Analyze data deterministically.** Return typed results with units and as-of dates. Compute valuation, growth, drawdowns, performance, exposure, and other numeric comparisons in code. The model can request another bounded calculation through explicit functions; it cannot write arbitrary SQL or run database updates.
5. **Plan and conduct web research.** Search by subquestion and entity, prioritize official/primary sources, use allowed domain/source-tier settings, open promising results, and follow up on gaps. Seek independent confirmation and a counterargument. Respect fetch limits and source terms. Do not assume “found on web” is equivalent to reliable.
6. **Assemble an evidence ledger.** Normalize private facts and web sources into evidence records with source ID/URL, type, publisher, publication and retrieval times, claim/passage, entity, period, units, trust tier, and any conflict or caveat. De-duplicate copies while retaining distinct updates.
7. **Synthesize into a validated report object.** Draft findings, opposing evidence, strategy fit, thesis implications, risks, unknowns and conclusion. Each material factual claim refers to evidence IDs. A conclusion can be “insufficient evidence” or “mixed”; the system should not force a buy/sell stance.
8. **Verify before save.** Check schema, citation IDs, quote/claim support, arithmetic, dates, units, required sections and strategy-version references. Flag unsupported or conflicting statements for regeneration or mark them explicitly as interpretation/uncertain. Save the final output plus run metadata and source inventory.
9. **Render and continue.** Show configurable sections, claim-level source links, freshness and confidence. Let the user correct an entity, evidence item or conclusion and create a thesis from the reviewed report. Never silently overwrite an existing thesis; present a proposed diff and preserve history.

## Improvement plan

### Phase 0 — Capability and quality baseline (1–2 weeks)

1. Inventory current database tables/read services and classify each dataset by entity, temporal coverage, freshness, quality flags, source/provenance, and current UI/API consumers.
2. Map current research/chat tools to that catalog. Record which tools are implemented, what arguments they accept, row/time bounds, citations/provenance returned, authorization path, and tests.
3. Run a baseline suite of representative prompts: quick fact, holding thesis, new ticker, earnings review, disputed news, portfolio exposure, macro question, counter-case, missing-data case, and non-equity/general research.
4. Label expected internal facts, external sources, strategy rules, calculations and unsupported claims for 30–50 cases. Score internal data recall, fact accuracy, numeric accuracy, citation support, strategy-rule traceability, balance, and schema adherence.
5. Instrument each research job with job/profile/prompt/model versions, retrieval tool/query/record IDs, web queries and URLs, token/cost and latency by step, retries, output validation and user feedback. Redact secrets and avoid retaining unnecessary raw page bodies.

**Exit criteria:** a current capability map, benchmark and trace for every run; reproducible failures categorized by retrieval, analysis, citation, strategy interpretation, or rendering.

### Phase 1 — Complete typed access to backend data (2–4 weeks)

1. Create an explicit **Research Data Catalog** mapping user questions to read-only domain tools and bounded arguments. Reuse existing service/query logic; do not expose arbitrary SQL to the model.
2. Ensure tools cover all research-relevant backend/database data: market quotes/history; fundamentals, statements and derived metrics; valuation outputs/assumptions; filings and extracted facts; news and event clusters; company/sector/market reference data; macro and FX; portfolio holdings, transactions, exposure and look-through; strategy rules, theses/KPIs and historical verdicts; and prior reports.
3. Normalize every returned field with source record IDs, units/currency, period, `as_of`, fetched/updated time, freshness and quality flags. Clearly encode `missing`, `not applicable`, `stale`, and `failed` separately from numeric zero.
4. Add high-level tools that answer common cross-table questions directly (e.g. “latest thesis KPI status and last two reports for entity X”) to avoid assembling fragile joins in prompts.
5. Bound date ranges and result counts, paginate deterministically, and support “more”/aggregation calls rather than returning excessive rows. Run under the backend’s least-privilege database role and user/tenant scope.

**Exit criteria:** benchmark questions can retrieve the required internal data; every returned fact is traceable to a source record and as-of date; tool access is read-only and bounded.

### Phase 2 — Research planning and web-source quality (2–4 weeks)

1. Add a planner output schema: question, subquestions, internal tools needed, web query intents, preferred/required source tiers, counter-evidence query, stop conditions, and planned report sections.
2. Turn research profiles into reusable **research modes**: quick answer, standard company/fundamental, deep thesis, earnings/thesis update, portfolio or macro, and custom. Profiles should control depth, maximum steps/cost, date horizon, web vs internal emphasis, domain allow/deny lists, language, peer comparison and selected output template.
3. Improve source policy: primary filing/issuer/regulator sources first for facts; quality news for context; specialist and secondary sources for interpretation; forums/social only when explicitly enabled and labeled. Permit user-supplied trusted domains without making them proof by default.
4. Require query iteration for deep mode: after initial evidence, identify unanswered questions, verify important claims with another source where practical, and search the strongest opposing case. Stop when required sections have sufficient evidence or the configured budget is reached.
5. Store all discovered URLs and access times even if they are not cited, distinguish search snippets from opened pages, and surface inaccessible/paywalled or undated sources as limitations.
6. Keep the provider integration behind an adapter so the current AI/web stack can evolve without coupling saved reports to a vendor-specific response format.

**Exit criteria:** on the benchmark, deep research retrieves additional unique, relevant evidence over one-shot search; source selection and limits are visible and reproducible.

### Phase 3 — Configurable output and thesis authoring (2–4 weeks)

1. Separate **research profile** (how to investigate) from **report template** (how to present results). Let users compose/reorder/enable sections and choose depth/language/format without editing system prompts.
2. Define a stable report envelope plus versioned template schemas. Example sections: question/scope, executive conclusion, business model, financial quality/trend, valuation (code-owned), moat/competition, catalysts, risks, bull/base/bear cases, strategy fit, thesis KPIs and exit conditions, evidence conflicts, unknowns, and sources.
3. Support both concise and detailed views over the same structured report. Allow Markdown/PDF/export as renderings of the canonical typed object. For truly custom output, allow a validated JSON Schema or a controlled field/section builder; cap size and reject unsafe schema features.
4. Make authoring modes explicit: **research only**, **draft thesis**, **propose thesis update**, and **review existing thesis**. For updates, show field-by-field changes and the evidence behind them; require the owner to accept before active configuration changes.
5. Preserve model-written analysis separately from code-derived figures and user-authored strategy. A template may reorganize content but cannot override trusted calculation or strategy fields.
6. Save immutable snapshots of template, research profile, prompt, strategy and thesis versions with each run so past conclusions remain interpretable.

**Exit criteria:** users can create and save a custom format, run reports under it, and preview/accept a proposed thesis without losing prior versions.

### Phase 4 — Claim-level grounding, balance and verification (2–4 weeks)

1. Persist each report statement as a typed claim (`fact`, `calculation`, `interpretation`, `strategy assessment`, `forecast/scenario`, or `unknown`) with evidence IDs, confidence, period, and review state.
2. Require citations on material factual claims and external assertions. For private database facts, cite the underlying source/record and show when it was updated. Cite calculations to inputs plus named formula/version.
3. Add a verifier pass that checks every citation target exists and supports the claim, figures agree with code outputs, units/periods align, and required sections/strategy rules are covered. It should detect unsupported citation mappings, not merely ensure citation syntax.
4. Require thesis/strategy comparisons to quote or name the specific configured rule/KPI, expected direction/threshold, current evidence, and status (`supports`, `challenges`, `mixed`, `unknown`). Keep qualitative fit separate from formal rule compliance.
5. Build balanced synthesis: strongest support, strongest challenge, base case, material risks, disconfirming evidence, assumptions, missing data and open questions. Never hide disagreement by averaging conflicting sources.
6. Use confidence labels tied to evidence completeness and source quality, not model self-confidence alone. Add explicit “as of” date, data coverage interval and stale/missing-data notice.

**Exit criteria:** material factual claims resolve to valid, relevant evidence; key strategy rules and counter-evidence are visible; arithmetic and dates pass deterministic checks.

### Phase 5 — Security, evaluation and iterative improvement (ongoing)

1. Treat all web pages, filings, feeds, stored news, documents and user-uploaded material as untrusted data. Tool permissions come from backend policy, never retrieved text. No research tool should send private portfolio/strategy data to arbitrary URL destinations.
2. Keep DB tools read-only; allow-list tool names, domains, arguments and result limits; validate/escape tool inputs; prevent SSRF and secret exposure in URL fetches; checkpoint and audit calls. A report may propose configuration changes but cannot apply them without the explicit user flow.
3. Keep traces keyed to a run but provide retention/deletion controls; minimize stored web text and respect existing provider terms and data-processing configuration.
4. Maintain an eval set that changes with holdings/data and includes retrieval coverage, entity ambiguity, false citations, numerical period/currency traps, stale data, prompt injection in a news/article body, conflicting sources, strategy changes, and unsupported conclusions.
5. Track helpfulness separately from truth: tool/data recall, factual accuracy, numeric accuracy, citation support, strategy alignment, counterargument coverage, unknown calibration, structure validity, cost, time to completion, and owner correction rate.
6. Review user feedback and failed high-impact reports; use adjudicated corrections to update data mappings, tool coverage, templates, prompts and benchmark examples.

**Exit criteria:** no unauthorized write path; injection tests do not expand access or trigger outbound data sharing; quality metrics and regression gates run for changes to prompts, models, tools, or templates.

## Suggested success measures

| Measure | What it tests | Pilot target |
|---|---|---:|
| Internal evidence recall | Expected relevant backend records actually retrieved | ≥95% on known-answer benchmark cases |
| Numeric correctness | Reported calculations match code recomputation, including units/periods | 100% for code-owned figures |
| Claim citation support | Material factual claims supported by cited source/record | ≥95% in human review |
| Strategy traceability | Conclusions that cite correct strategy/thesis rule and version | ≥95% in applicable cases |
| Counter-evidence coverage | Reports include material contradictory evidence found in benchmark | ≥90% on balanced cases |
| Schema validity | Output conforms to selected, versioned report structure | 100% after validation |
| Useful completion rate | Research jobs accepted with no substantive correction | Establish baseline, then improve by mode |
| Run efficiency | Cost and p50/p95 duration by profile, with useful-evidence yield | Set budgets after baseline |

Use a human-reviewed set and segment by job type and data family. Targets are pilot gates, not claims about current performance.

## Recommended implementation order

1. Audit current code/config and baseline it; preserve existing research UI and pipeline.
2. Close read-only data access gaps and return freshness/provenance uniformly.
3. Add a structured research plan plus iterative, source-tier-aware web search.
4. Add versioned, configurable report templates and draft-thesis workflow.
5. Add a claim ledger/verifier and balanced synthesis requirements.
6. Add security regression coverage, production traces and quality/cost gates.

## Decisions to capture during Phase 0

The work can begin with code/config inventory without further decisions. The following choices affect later interfaces and budget: whether custom formats are selected sections or user-authored schemas; supported export formats; whether research should consider the full portfolio by default or only explicit context; which language(s) and source regions are first-class; and cost/latency budgets for quick vs deep modes. A reasonable default is a section builder over a fixed canonical schema, with arbitrary JSON Schema as a later advanced option.

## Research references

- [OpenAI Deep Research API guide](https://developers.openai.com/api/docs/guides/deep-research)
- [OpenAI Web Search API guide](https://developers.openai.com/api/docs/guides/tools-web-search)
- [OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI Evals guidance](https://developers.openai.com/learn/evals)
- [Anthropic Web Search tool documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- [OWASP Prompt Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html)
- [OWASP RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)
- [OpenAI BrowseComp browsing-agent benchmark](https://openai.com/index/browsecomp/)
