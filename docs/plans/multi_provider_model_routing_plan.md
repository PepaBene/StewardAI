# Multi-provider model routing plan

Move AI work off Anthropic-only onto three tiers. Plan only; no code changed yet.

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
| budget `downgrade_model` | Haiku 4.5 | Becomes luna |

## What has to be built

1. **Provider interface** in front of `steward/ai.py`: one `complete()` call taking messages, tools, a pydantic output schema and an effort level, returning a normalised result and `Usage`. Call sites stop calling `messages.parse` directly (18 sites).
2. **Adapters**: OpenAI (Responses API), Google (`google-genai`); Anthropic stays for rollback.
3. **Budget**: `BudgetedAnthropic` only guards Anthropic. Move the guard into the interface so every provider is capped and counted. `_downgrade` is Anthropic-specific and needs a per-provider version.
4. **Price table**: `BudgetConfig` has one set of cache multipliers and one search price. Make both per model. OpenAI cached input is about 0.05x of input with no write premium; Gemini cache read is 0.1x; search is $10 or $14 per 1k.
5. **Config**: `ModelId` is a Literal of Claude IDs; extend it and keep older saved config versions valid.
6. **Structured output**: map `output_format=SectionDraft` to each provider's schema mode; keep the parse-failure handling.
7. **Search**: the `pause_turn` loop and `result_titles` in `research/writer.py` are Anthropic-specific. Each provider needs its own citation extraction.
8. **Batch**: `digest.py` and `monthly.py` use Anthropic batches. Both OpenAI and Google offer batch; each needs its own submit/poll/collect.

## Verify before building (could change the routing)

- Gemini: can Google Search grounding be combined with structured JSON output in one request on 3.8 Flash? Is there any domain allowlist? `research` relies on `allowed_domains` and `source_tiers`. If not, enforce tiers by rejecting citations after the fact, or keep search jobs on sol.
- OpenAI: does `gpt-6-luna` support the web search tool and structured output? Is it billed as a reasoning model (search content tokens then count)?
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

## Gates

- Earnings: golden cases pass with no new wrong figures.
- News classification: accuracy on the labelled set.
- Research: sections are factually correct, cite only allowed tiers, parse into `SectionDraft`.
- Cost: compare per-run cost from `job_step` before and after, not per-token price.
