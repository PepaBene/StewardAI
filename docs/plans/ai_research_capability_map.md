# AI research capability map (phase 0 baseline)

**Date:** 2026-10-11
**Companion to:** [ai_research_thesis_improvement_plan.md](ai_research_thesis_improvement_plan.md)

What the research and chat features can reach today, what the benchmark measures, and what
each run records. The two tables below are generated from `steward.chat.catalog` and held
against the real tool list by `tests/test_research_catalog.py`; edit the catalog, then
regenerate the block between the markers (the test prints which block is stale).

## Datasets and the chat tools that read them

Every tool is read-only, takes bounded arguments (no SQL from the model), registers what it
returns in the source registry so the answer can cite it, and stamps its result with `tool`
and `retrieved_at`. Tools added in phase 1 also report each figure in the shared envelope
(`steward/chat/envelope.py`): `status` (`ok`, `missing`, `not_applicable`, `stale`,
`failed`), `value` (null unless `ok` or `stale`), `unit`, `currency`, `period`, `as_of`,
`source_id` and `reason`. A missing figure is never zero.

<!-- capability-map:start -->
| Dataset | Tables | Chat tools | Updated | Gap in chat |
|---|---|---|---|---|
| Portfolio positions, cash, allocation and drift | `account_snapshot`, `position_snapshot`, `instrument` | `portfolio_overview`, `holding`, `over_cap_positions`, `theme_exposure` | daily sync | none |
| Orders, dividends and cash movements | `transaction`, `transaction_fee` | `transactions`, `decision_scorecard`, `performance_explain` | daily sync | none |
| Daily closes, quotes and FX | `price_bar`, `market_quote`, `fx_rate` | `price_history`, `fx_rates`, `latest_quote`, `listing_liquidity` | daily; quotes intraday | No total-return or adjusted series; benchmark comparison is price return only. |
| Filed statements and the ratios computed from them | `financial_statement_fact`, `fundamentals`, `xbrl_filing` | `fundamentals`, `holding`, `statement_lines`, `share_counts`, `segment_revenue` | on each filing | Statement lines and segments come only from stored filings (SEC XBRL and parsed facts); other markets have no raw lines. |
| Scenario DCF, reverse DCF and reference multiples | `company_valuation_scenario` | `valuation_scenarios`, `start_size`, `idea_factor_fit` | computed on request | none |
| Filed notes, control stakes, 13F, N-PORT, insider activity, deals, dependencies, consensus | `fact`, `evidence`, `control_stake_filing`, `form13f_filing`, `nport_holding`, `insider_filing`, `deal_event`, `entity_relationship`, `estimate_value` | `filing_evidence`, `company_context`, `event_paths`, `event_feed` | on each filing | none |
| Passages of archived filings and results releases, your reports, theses and notes, and news | `document_chunk` | `search_documents` | daily index run | Only documents the index job has processed; filings that were never archived (fetched on the fly by the exposure builder) and disclosure-register documents are not indexed yet. |
| The owner's working notes for the assistant, general or per holding | `agent_note` | `agent_notes` | when the owner edits them | none |
| Stored news, wire articles, story clusters and threads | `news_item`, `news_article`, `story_cluster`, `story_thread`, `news_embedding` | `search_news`, `search_wire`, `get_story_thread`, `event_feed` | continuous | none |
| Macro and commodity series; country context | `macro_observation`, `macro_series` | `macro_series`, `country_context` | per series release | none |
| Sector, country and industry context for a company | `issuer`, `security`, `listing` | `company_context`, `country_context`, `corporate_actions`, `peers` | on change | none |
| The owner's strategy: goals, targets, hard limits, rules, risk limits | `config_version` | `strategy_rules`, `portfolio_overview` | owner edits | none |
| Per-holding theses, KPIs, verdicts and the reports written about them | `config_version`, `verdict`, `report`, `thesis_memo`, `portfolio_review` | `thesis_and_reports`, `holding`, `search_reports`, `thesis_memos`, `portfolio_review` | owner edits; verdicts on each earnings check | none |
| Ex-ante risk, factor tilts, stress tests, past episodes and their history | `risk_report_snapshot`, `factor_return`, `risk_scenario` | `risk_forecast`, `stress_test`, `past_episodes`, `factor_tilts`, `model_check`, `position_risk`, `risk_whatif`, `risk_history`, `idea_factor_fit` | daily | none |
| Sizing, deposits, rotation and switch effects | `switch_log` | `start_size`, `deposit_allocation`, `rotation_candidates`, `switch_effect` | computed on request | none |
| Fund holdings, look-through and overlap | `etf_holding`, `etf_snapshot`, `nport_holding` | `fund_overlap`, `etf_breakdown`, `fund_nav` | per fund filing | none |
| When each dataset was last updated, and provider coverage | `coverage_status`, `sync_state` | `data_freshness`, `coverage_review` | live | none |
| Open web through the model provider's search, limited to the allowed outlet tiers | — | — | live | Provider-side search; the URLs it returns are not stored unless cited. |
| Proposed jobs the owner confirms | `chat_action` | `propose_job` | live | none |

| Tool | Datasets | Wraps | Bounds | Scope | Provenance |
|---|---|---|---|---|---|
| `portfolio_overview` | portfolio, strategy | `portfolio.read.portfolio_overview` | one snapshot | owner | snapshot date as source |
| `holding` | portfolio, fundamentals, theses | `earnings.read.holding_detail` | 30 verified facts | owner | fact id and filing URL per fact |
| `search_news` | news | `SQL full-text on news_item` | 15 items, 365 days | global | news URL, outlet tier |
| `search_wire` | news | `SQL full-text and embeddings on news_article` | 15 items, 90 days | global | article URL, outlet tier |
| `get_story_thread` | news | `news.read_analysis.thread_detail` | one thread | global | story ids |
| `search_reports` | theses | `SQL full-text on report` | 8 reports | owner | report id |
| `country_context` | macro, reference | `data_context.research.country_context` | as-of cutoff | global | claim ids with known-as-of |
| `company_context` | filed_evidence, reference | `data_context.research.company_context` | as-of cutoff | global | claim ids with known-as-of |
| `stress_test` | risk | `analytics_tools.stress_test` | configured episodes | owner | episode and method version |
| `risk_forecast` | risk | `analytics_tools.risk_forecast` | latest report | owner | method version |
| `past_episodes` | risk | `analytics_tools.past_episodes` | configured episodes | owner | episode dates |
| `factor_tilts` | risk | `analytics_tools.factor_tilts` | one region | owner | factor model version |
| `idea_factor_fit` | risk, valuation | `analytics_tools.idea_factor_fit` | few symbols | owner | factor model version |
| `model_check` | risk | `analytics_tools.model_check` | latest report | owner | method version |
| `position_risk` | risk | `analytics_tools.position_risk` | one position | owner | method version |
| `risk_whatif` | risk | `analytics_tools.risk_whatif` | few trades | owner | method version |
| `start_size` | valuation, trade_planning | `analytics_tools.start_size_for` | one ticker | owner | strategy thresholds |
| `deposit_allocation` | trade_planning | `analytics_tools.deposit_allocation` | one amount | owner | strategy targets |
| `rotation_candidates` | trade_planning | `analytics_tools.rotation_candidates` | ranked list | owner | strategy targets |
| `switch_effect` | trade_planning | `analytics_tools.switch_effect` | one pair | owner | method version |
| `over_cap_positions` | portfolio | `analytics_tools.over_cap_positions` | positions over cap | owner | strategy limits |
| `theme_exposure` | portfolio | `analytics_tools.theme` | one theme | owner | mapping ids |
| `event_paths` | filed_evidence | `analytics_tools.event_reach` | bounded graph walk | owner | edge ids |
| `search_documents` | documents | `retrieval.search.search (full text + embeddings, RRF)` | 20 passages, 2 per document | owner | passage text with source URL or app page and date |
| `agent_notes` | agent_notes | `SQL on agent_note` | 20 active notes | owner | note id and edit date |
| `thesis_memos` | theses | `SQL on thesis_memo` | 8 memos, or 3 in full for one ticker | owner | memo id and date |
| `portfolio_review` | theses | `SQL on portfolio_review` | the latest review | owner | review id and date |
| `event_feed` | filed_evidence, news | `events.feed.build` | 25 items, 90 days | owner | story URL and outlet tier per item |
| `fund_overlap` | funds | `analytics_tools.fund_overlap` | one ticker | owner | fund snapshot |
| `performance_explain` | transactions | `analytics_tools.performance_explain` | up to a year | owner | snapshot dates |
| `decision_scorecard` | transactions | `analytics_tools.decision_scorecard` | last year | owner | journal ids |
| `propose_job` | actions | `chat_action insert` | one proposal | owner | action id |
| `filing_evidence` | filed_evidence | `data_context.evidence` | 10 items by default, 25 at most, as-of cutoff | global | source URL, passage and known-at per item |
| `fundamentals` | fundamentals | `company.ratios.build_ratios` | 80 ratios, or 300 points with periods | global | ratio group source with filed periods |
| `valuation_scenarios` | valuation | `company.valuation.build_valuation` | 5 scenarios: the owner's newest saved set, else defaults | owner | method version, price date and whether the assumptions are saved or default |
| `price_history` | prices | `company.service.load_market` | 120 points, 10 years, optional benchmark | global | last close date, thinning stated |
| `fx_rates` | prices | `SQL on fx_rate` | 60 fixings, 365 days | global | fixing date and source |
| `latest_quote` | prices | `SQL on market_quote` | latest per provider | global | quote time and provider |
| `listing_liquidity` | prices | `SQL on listing_liquidity, price_bar` | 30 daily bars | global | ISIN record and last bar date |
| `statement_lines` | fundamentals | `SQL on financial_statement_fact` | 60 lines | global | filing URL per line |
| `share_counts` | fundamentals | `SQL on shares_outstanding` | 8 records | global | method and as-of date |
| `segment_revenue` | fundamentals | `SQL on segment_revenue, xbrl_filing` | 60 rows | global | filing instance URL |
| `corporate_actions` | reference | `SQL on corporate_action` | 60 actions, 10 years | global | source URL per action |
| `peers` | reference | `SQL on peer_set` | 25 peers | global | peer basis and status |
| `etf_breakdown` | funds | `SQL on etf_snapshot, etf_holding` | 25 holdings, 10 groups | global | snapshot date |
| `fund_nav` | funds | `SQL on fund_nav_point` | 30 points | owner | NAV source URL |
| `coverage_review` | freshness | `SQL on coverage_status` | 60 rows | global | provider, dataset and checked time |
| `macro_series` | macro | `SQL on macro_observation` | 60 periods | global | source URL and vintage |
| `strategy_rules` | strategy | `config.store.load_active` | one version | owner | config version id |
| `transactions` | transactions | `portfolio.transactions.list_transactions` | 50 per page, cursor | owner | scope and totals over all matches |
| `risk_history` | risk | `SQL on risk_report_snapshot` | 30 snapshots | owner | snapshot id and method version |
| `thesis_and_reports` | theses | `config.load_active, verdict, report` | last 2 reports | owner | config version, verdict and report ids |
| `data_freshness` | freshness | `SQL max() probes, coverage_status` | 8 datasets | global | latest record date per dataset |
<!-- capability-map:end -->

## Gaps that remain after phase 1

The full list, by phase, is in "Gaps left after Phases 0 and 1" in
[ai_research_thesis_improvement_plan.md](ai_research_thesis_improvement_plan.md). The
data-related ones:

- **Retrieval over prose** (filings, reports, theses) is the agentic and RAG plan's AG2, not
  this plan. News already has embeddings.
- **Web search** is the provider's own. Research reports now keep every URL the provider
  returned (`discovered_urls`: status cited, found-not-cited or excluded, with the reason and
  access time). The provider exposes only the pages its answer cited, so this is not every page
  it looked at.
- **FX and intraday quotes** are read by the `fx_rates` and `latest_quote` tools (fixings are
  per source and never mixed across the EUR and CZK bases). The research run can call them
  through its plan; the portfolio overview reads them separately.
- **Saved valuation scenarios** are written by the Company page but `valuation_scenarios`
  reads default assumptions only.
- **Tools written before phase 1** (`stress_test`, `risk_forecast`, ...) return their own
  shapes. Their results are stamped with `tool` and `retrieved_at` and registered as sources,
  but figures inside them are not yet in the envelope.

## Baseline benchmark

`apps/backend/evals/research_cases.json` holds the labelled cases: ten categories (quick
fact, holding thesis, new ticker, earnings review, disputed news, portfolio exposure, macro,
counter-case, missing data, general research), at least three each. A case labels:

| Label | Meaning |
|---|---|
| `tool_groups` | Groups of tools; a good run calls at least one tool from every group. |
| `datasets` | Catalog datasets the answer depends on. |
| `strategy_rules` | Rules, KPIs or thresholds the answer must name with their version. |
| `code_owned` | Figures that must come from code, not the model. |
| `must_admit` | Gaps, staleness or limits the answer must state. |
| `unsupported_traps` | Claims that must not appear. |

`steward/chat/benchmark.py` validates the case set against the catalog (unknown tools, an
unexercised tool, a category under three cases fail the build) and scores a run's tool trace:
**tool recall** (share of groups reached), failed calls and unexpected action tools. A
failure is filed under the stage it broke at; the trace alone can only show `retrieval`.
Fact accuracy, numeric accuracy, citation support, strategy traceability and balance are
reviewed by a person against the labels, and the failure is then filed under `analysis`,
`citation`, `strategy` or `rendering`.

## Run trace

Each chat answer job records on its `answer` step (`job_step.detail`), per tool call: the
tool, its (clipped) arguments, duration in ms, whether it returned data or an error, how many
citable records it added and its result size. The step also records the model, the number of
sources, the answer shape and token usage (`job_step.usage`), and the job records the config
versions it ran under. Together these give the retrieval trace the benchmark scores.

The research report job records, on the report itself: the plan, the follow-up rounds, every
URL found with its status, the search summary and a snapshot of the profile, template, prompts
and config versions it ran under. Its `job_step` rows hold usage per section and follow-up.

The research job also keeps a per-call trace on the report (`research/trace.py`: model calls,
web queries and URLs, sources offered against cited, validation drops), and its stored-data
tool calls are listed in `tool_calls`. Not yet traced: the search queries the provider ran
(the provider runs them) and user feedback linked to a run.
