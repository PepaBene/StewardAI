# StewardAI: agentic workflows, RAG and whole-app AI access

Research snapshot: 5 October 2026. Builds on [ai_research_thesis_improvement_plan.md](ai_research_thesis_improvement_plan.md) (grounded research, claim ledger, verifier, evals), which stays the quality spec. This plan answers a different question: **how the AI gets to all of the app's data, the wider world and the owner's portfolio, and which parts need RAG, which need agents.** It changes no code.

## Verdict

1. **Do not build "RAG over everything".** Almost all of the app's data is structured (about 150 tables: positions, prices, fundamentals, macro, ownership, insider filings, graph edges, risk). The model should reach it through typed read-only tools, not through embeddings. Embeddings are for the minority that is prose.
2. **Build one hybrid text-retrieval tool** (keyword + vector, fused) over the prose corpus: filings, IR releases, news, reports, theses, the owner's notes.
3. **Make the agent loop the backbone and give it more tools.** Chat already is one; research is still a fixed pipeline and should become a planned, multi-agent run in deep mode.
4. **Add event-driven agents** that react to alerts, news and earnings and leave proposals in an inbox. This matches the strategy (event-driven rotation priced for risk) better than a chat box does.
5. **Everything stays read-only and owner-confirmed**, as `propose_job` is today.

## What exists today (verified in code, 5 Oct 2026)

| Piece | State |
| --- | --- |
| Chat (`steward/chat/assistant.py`, `tools.py`) | Already agentic: a Claude tool-use loop with a step cap, server-side web search limited to source tiers, sentence-level citations, claim checking, and an "insufficient evidence" path. **Seven tools only**: `portfolio_overview`, `holding`, `search_news`, `search_reports`, `country_context`, `company_context`, `propose_job`. |
| Research (`steward/research/run.py`, `gather.py`) | A fixed, checkpointed chain: code gathers figures, then one model call per section, then the claim verifier. Not agentic: the model cannot decide to look something else up in the app. |
| Retrieval | Postgres full-text only (`news_item.search`, `report.search`, GIN indexes). **No embeddings, no pgvector.** |
| Prose already in the database | `news_item`, `report`, `source_document` / `source_document_version`, `issuer_ir_release`, `company_research_observation`, filing passages (`graph/passages.py`), theses and verdict text. |
| Plumbing to reuse | Job queue with `job_checkpoint`, `BudgetedAnthropic` client with per-job caps (`ai.py`, `budget_guard.py`), `verify/` (claims, evals, replay, feedback), `Registry` of citable sources. |

Biggest gap: the chat agent can see a small fraction of what the app knows. Price history, fundamentals, risk and correlation, insider and 13F data, macro, fund look-through, the company graph, corporate events, strategy rules and the transaction log are not reachable from chat.

## Research findings

- **Agentic search often beats classic RAG on filings.** On FinanceBench an agentic keyword-search setup beat a traditional RAG pipeline by about 6 points of answer correctness ([Is Agentic RAG worth it?](https://arxiv.org/pdf/2601.07711)); coding agents moved away from vector indexes to tool-driven search for staleness and simplicity reasons ([overview](https://buzzgrewal.medium.com/ai-agents-dont-need-vector-search-anymore-inside-the-agentic-search-stack-replacing-rag-in-2026-58efcabe4f6f)). Treat these as directional, not proof for this app.
- **But retrieval quality dominates financial accuracy.** Reported FinanceBench numbers show a large gap between oracle context and basic retrieval ([summary](https://intuitionlabs.ai/articles/llm-financial-document-analysis)), and standard chunking corrupts tables. Filings need structure-aware parsing and parent/child chunks (small child chunks to match, parent section to read) ([FinSage](https://arxiv.org/pdf/2504.14493)).
- **Hybrid beats vector-only.** In Postgres, pg_trgm/tsvector plus pgvector fused with Reciprocal Rank Fusion is reported well ahead of pure vector search ([write-up](https://callsphere.ai/blog/vw7h-pg-trgm-pgvector-hybrid-retrieval-2026)). Marketing-grade numbers: validate on your own eval set. Supabase Postgres already runs here, so no new datastore.
- **Route by question, don't make everything agentic.** Plain hybrid retrieval serves most lookups; agentic loops earn their extra cost on complex questions ([adaptive routing](https://rdp.in/gpu-mart/knowledge-base/adaptive-rag-2026-hybrid-graph-agentic-tiers/)).
- **Claude platform features worth using:** programmatic tool calling lets the model write code that calls your tools and returns only aggregates, which suits "scan 40 holdings" jobs; it cannot call web search, web fetch or MCP-connector tools ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)). Web fetch is a server tool ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool)). Scope tool access tightly; fewer tools mean fewer mistakes ([tool-use overview](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)).
- **Not verified:** exact current model prices/limits and whether the Claude Agent SDK or Managed Agents beat the hand-rolled loop here. Check before Phase 3 (use the `claude-api` skill).

## Architecture: four access layers

| Layer | Data | How the AI reaches it | RAG? |
| --- | --- | --- | --- |
| A. Structured | Positions, transactions, prices, fundamentals, risk, macro, ownership, insiders, graph, events, strategy, theses, verdicts | Typed, bounded, read-only tools with `source_id`, `as_of`, units | No |
| B. Prose corpus | Filings, IR releases, transcripts, news, reports, theses, owner notes | One `search_documents` tool: hybrid keyword + vector, returns passages with citation ids | Yes |
| C. The open web | Anything else | Existing web search/fetch on allowed tiers; fetched pages never mixed with private data | No (live) |
| D. Agent memory | Past runs, owner corrections, per-holding working notes | Reports, verdicts and feedback are already stored; add a small `agent_note` table the agent reads and the owner can edit | Light |

Portfolio access is layer A and is the easy part: it only needs more tools, always scoped to the question (a one-ticker question does not load the whole book).

## Agent design

- **Router first.** Cheap classification: quick lookup (one tool call, small model), chat question (tool loop), deep research (planned multi-agent run), event reaction (triggered run). Matches the profile modes in the existing plan.
- **Deep research as orchestrator + specialists.** Planner writes the brief; parallel workers each own one lens (financials and valuation, filings and events, news and web, portfolio fit and risk, counter-case); a synthesizer drafts into the typed report; the existing claim verifier gates it. Each worker's output is a `job_checkpoint`, so the resume behaviour already in `run.py` carries over. Respect the user's rule of thumb: at most 2 workers of this kind of heavy work running at once, enforced by the job budget.
- **Code owns numbers.** Valuation, risk, drift and returns stay computed in Python; the model requests them via tools and never recomputes.
- **Event-driven agents.** Triggers already exist (alerts, `free_source_alerts`, earnings checks, `monitor`). Each trigger starts a bounded run that writes an inbox item: what happened, which thesis KPI or rule it touches, evidence, proposed action. It never trades and never edits a thesis without confirmation.
- **Tool design.** Few, high-level tools (a ticker "dossier", "risk of proposed trade") beat many raw table readers. Return concise JSON; page with "more" calls.

## Phases

**AG0: Tool catalog and eval seed (1 week).** List every dataset against the chat tool list; rank gaps by how often the strategy needs them (risk and correlation, price history, fundamentals and forward estimates, insider/13F, events, macro, transactions, strategy rules, graph). Add 15 chat questions with known answers to the existing eval harness (`verify/eval.py`). *Verify: a written gap list and a scored baseline.*

**AG1: Widen the structured tools (2–3 weeks).** Add the top-gap tools to `chat/tools.py`, reusing existing read services (no new SQL exposed to the model). Every result carries `source_id`, `as_of`, units and an explicit missing/stale marker. *Verify: baseline questions now answerable; tools read-only (test with a read-only DB role).*

**AG2: Prose corpus and hybrid retrieval (3–4 weeks).** Enable `pgvector`; add a `document_chunk` table (document id and version, section path, parent id, text, tsvector, embedding, as_of, entity ids). Parse filings by section and table before chunking; index only new document versions; keep the embedding model name on each row so a swap can re-embed. Expose `search_documents(query, ticker?, kinds?, date range?)` fusing both rankings with RRF, optionally with a cross-encoder rerank if the eval shows it helps. Corpus: tiers 1–3 (filings and IR, own reports/theses/verdicts, news), embedded with Google `gemini-embedding-2` (decision 1). Replace `search_news` / `search_reports` with it once it scores at least as well. *Verify: retrieval recall@k and citation support on the eval set, hybrid vs keyword-only, before keeping the vectors.*

**AG3: Agentic deep research (3–4 weeks).** Planner, specialist workers, synthesizer on the existing queue; a profile switch keeps the current fixed pipeline for cheap runs. *Verify: on the benchmark, deep mode finds more correct evidence than the fixed pipeline at a cost within the profile's budget.*

**AG4: Event-driven agents and inbox (2–3 weeks).** Trigger → bounded run → inbox item with evidence and a proposed action; owner confirms through the existing chat-action flow. Inbox only; optional WhatsApp push for thesis-KPI and risk-breach events afterwards (decision 3). *Verify: replay of 10 past events produces useful items with no unconfirmed write.*

**AG5: Memory and ongoing evaluation.** Owner-editable `agent_note`; feedback loop into evals; cost and latency dashboards per route.

Sequencing note: AG0–AG1 are cheap and can run alongside the Morningstar and quant plans' early phases; AG2 onward comes after them. AG1's risk tools need the quant plan's `Q1` and `Q8` outputs, so build those tools against today's per-stock risk data first.

## Risks and controls

- **Prompt injection** from filings, news and web pages: retrieved text is data; tools and write paths are fixed by backend policy; no tool can send private data to a URL the model supplies (OWASP [prompt injection](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html) and [RAG security](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)). Add an injected-news-article case to the evals.
- **Privacy:** portfolio and strategy are sent to the model provider on every call; keep the existing redaction (`redact.py`) and confirm the provider's data-retention terms. Embeddings add a second vendor (Google): the owner's own reports and theses are embedded there, so check Google's data-use terms and keep any portfolio-sizing detail out of embedded text where practical.
- **Cost:** agent loops multiply calls. Keep `BudgetedAnthropic` caps per route, cache stable prompt prefixes, use a small model for routing and verification, and log cost per route.
- **Stale and wrong figures:** the claim verifier and "code owns numbers" rules from the research plan apply to every route, including the new ones.
- **Complexity:** each layer is added only if the eval shows a gain; AG2 may end with keyword-only if vectors do not help.

## Decisions (owner, 5 October 2026)

1. **Embeddings: Google Gemini embeddings (hosted).** Use **`gemini-embedding-2`** (stable, the model Google's docs recommend as newest, updated April 2026), not `gemini-embedding-001` ([docs](https://ai.google.dev/gemini-api/docs/embeddings)). It takes up to 8,192 input tokens (001: 2,048), which fits filing sections and parent chunks better; output is 3,072 dimensions by default, adjustable from 128 to 3,072 (recommended 768, 1,536 or 3,072); it is multimodal (text, image, video, audio, PDF), so filings' PDFs and charts could later be embedded directly. Differences from 001 that affect the build: the task is stated in the prompt text (for example `task: search result | query: {content}`), not through a `task_type` parameter, and the two models' vector spaces are incompatible, so never mix them in one index. The Batch API is 50% of the default embedding price, which suits the bulk backfill of filings and news. Text price was about $0.20 per 1M tokens on a third-party page ([source](https://openrouter.ai/google/gemini-embedding-2)): the docs page I read gave no price, so confirm it on Google's pricing page. Before AG2: compare 768 vs 1,536 dimensions on the eval set (storage is the only cost that scales with dimension; start at 768 unless 1,536 clearly retrieves better); confirm the paid-tier data-use terms (not verified), because tier 2 sends the owner's own reports and theses to Google. Model name and dimension are stored on each row, so a later switch means re-embedding, not a redesign.
2. **Corpus: tiers 1–3.** Filings and IR releases (holdings and watchlist companies), the owner's own reports, theses and verdicts, and news. Consequences: news is the high-volume, fast-stale tier, so index only new items, keep a retention window (for example 12–24 months) for vectors while keeping full-text search over everything, and embed headline + summary, not full article bodies. Tier 4 (uploaded notes and PDFs) is out of scope for now.
3. **Proactivity: inbox only, with WhatsApp as an optional later channel.** Inbox ships in AG4. WhatsApp is an AG4 add-on, built only if the inbox proves useful. It would use Meta's WhatsApp Business Platform (Cloud API); access is free and messages are charged only outside an open 24-hour service window, where a pre-approved utility template is needed (about $0.004 per message in the US; Czech rates not checked) ([pricing overview](https://www.unipile.com/whatsapp-business-api-pricing/)). Replies inside a window are free, so a "reply to open the window" habit keeps cost near zero. Not verified: whether Meta allows a personal-use setup like this without a business account, and the exact approval steps. Check before committing; Telegram or email are fallbacks. Only events that touch a thesis KPI or a risk-limit breach would ever push.
4. **Budget: generous ceilings, enforced by the existing guard.** Principle: the owner should never be blocked in normal use, and nothing should run away unattended. Concrete shape (numbers set after AG0–AG1 from `job_step` cost data, at about 2× the median run):
   - chat and deep research: per-run caps high enough for normal use, and a shared monthly ceiling;
   - event agents (unattended): the strictest per-run and monthly caps, and they stop with "budget reached" rather than overrun;
   - at about 80% of the monthly ceiling: a visible warning; at the ceiling: `budget_guard` drops to the cheaper model instead of refusing, as it already can;
   - routing, claim verification and embeddings use the cheapest adequate model; cache stable prompt prefixes.
5. **Framework: keep the current Claude loop.** Revisit only if AG3's orchestration gets unwieldy; tools built now work under either approach.

## Remaining open items

- Exact monthly figures (after AG0–AG1 cost data).
- Retention window for news vectors.
- Whether WhatsApp is feasible for a personal setup (check before AG4 design).
- Google data-use terms for the embedding API.
