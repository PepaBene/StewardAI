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
| 2 Planning and web quality | Not started | Research is still a fixed chain with a gap finder (`research/gaps.py`) and source-tier-limited web search; there is no planner schema, no deep-mode query iteration, and no counter-evidence search step. Profiles already control sections, depth, tiers, peers, language and budgets. |
| 3 Output and thesis authoring | Partial | Exists: configurable profile sections (code-written vs model-written split), a thesis first-draft generator (`thesis/draft.py`: facts-only prompt, lines that cite no fact are dropped, nothing saved until the owner edits), bear case, per-figure sources and server-side PDF (Morningstar plan). Remains: separate report templates from profiles, versioned template schemas, thesis *update* proposals as field-by-field diffs, immutable snapshots of template/profile/prompt/strategy versions per run. |
| 4 Claim grounding | Partial | Exists: sentence-level source IDs, claim verifier over every model sentence, "insufficient evidence" path, code-owned numbers. Remains: typed claim ledger (fact / calculation / interpretation / forecast), strategy-rule status per claim, confidence tied to evidence coverage. The news engine has its own claim checks (`news/claims.py`) that could share the ledger format. |
| 5 Security and eval | Partial | Exists: read-only tool design, owner-confirmed `propose_job`, budget guard and routing, redaction. Remains: injected-article regression case, retention/deletion controls for traces, research-specific regression gates on prompt/model/tool changes. |

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
2. The research report job does not use the new tools. It still reads data through `gather`, so research and chat can disagree on freshness and shape.
3. No tools for FX rates, intraday quotes, listing liquidity, raw statement lines, segment revenue, share counts, corporate actions, peers, ETF breakdowns, fund NAV and factsheets, patents, procurement awards, or the coverage and quality review queues.
4. `valuation_scenarios` uses default assumptions only and ignores the owner's saved scenarios. It builds ratios without peers, so peer comparisons are missing.
5. `fundamentals` returns the latest value per ratio, not the series. Multi-period trends need a further call or tool.
6. `filing_evidence` returns one section per call, cut to a few items with long passages shortened; there is no pagination or "more" call for it, `macro_series` or `risk_history`.
7. `risk_history` shows top-level numbers only, and cannot compare snapshots that have different method versions.
8. `price_history` covers daily closes only, thinned to 120 points, with no benchmark or total-return series.
9. The "prior reports" side of `thesis_and_reports` returns the last two research or review reports with 600-character excerpts and no structured sections. Digest and escalation reports are excluded.
10. Freshness limits per dataset in `data_freshness` are fixed constants, not configuration; it probes eight datasets and does not cover filings, 13F, N-PORT or consensus per ticker.
11. Source records for tool figures carry a snippet of the values, not the filing passage; ratio and valuation sources point at the company page rather than a stored record id.
12. No least-privilege check: tools run under the backend role, and scope is "owner" by convention (owner-only data is not filtered per user because the app has one owner).
13. Tool descriptions and the system-prompt addition are not evaluated: the chat component's eval fingerprint will change and `evals/baseline.json` needs a re-run with `--write-baseline` before the eval gate passes.

**Test coverage of the new code**

1. `tests/test_research_tools_db.py` covers strategy, transactions, risk history, macro, thesis and reports, evidence coverage, freshness and uncovered-ticker errors. The success paths of `fundamentals`, `valuation_scenarios` and `price_history` (a covered company with statements and prices) and of `filing_evidence` with real records are not tested, because they need a seeded filing and price fixture.
2. No test drives the model loop with the new tools, so tool selection by the model is unmeasured until the live runner exists.
3. The older tests that construct `ChatTools` or compare exact tool results were not re-run after results gained `tool` and `retrieved_at`.

**Later phases (unchanged, not started)**

Phase 2 planner, query iteration and counter-evidence search; Phase 3 report templates, thesis update diffs and immutable run snapshots; Phase 4 typed claim ledger and strategy-rule status per claim; Phase 5 injected-article regression case, retention and deletion, and regression gates. See the table above.

## Current architecture and product intent

From the repository’s technical description and code layout:

- Python FastAPI receives chat and research actions; AI work runs in a persistent worker through a database queue. Research steps are checkpointed to avoid repeating billed work after interruption.
- Research is already designed as configurable profiles and sections. Profiles choose depth, models, source tiers, peers, language, and budgets.
- Research draws on current company/fundamentals/valuation data, filings, news, market/portfolio information and the web. Code is intended to calculate numbers; model prose explains them.
- Existing reports and UI handle structured research sections, valuation, sentence-level source IDs and source cards. The app also stores strategy, per-holding theses/KPIs, prompts and configuration history.
- Chat is intended to query backend data, search/fetch the web, answer with sources, and start a job when the question merits a longer report.

Relevant implementation areas include `apps/backend/src/steward/research/`, `apps/backend/src/steward/chat/`, `apps/backend/src/steward/config/research.py`, `apps/backend/src/api/research_routes.py`, `apps/backend/src/api/chat_routes.py`, and `apps/web/src/lib/research/report.ts`. Since this section was written, `steward.data_context`, `steward.thesis`, `steward.verify`, `steward.decision`, `steward.news` (analysis engine, wire, embeddings) and `steward.events` have been added or grown; see the status table above. During implementation, inventory actual read tools, source records, schemas and profile validation against this plan before changing them; this plan does not assume every capability described in the product document is fully implemented in the current runtime.

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
