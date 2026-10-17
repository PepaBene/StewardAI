# AI cost reduction plan (Gemini and OpenAI)

Written 2026-10-17 from production data (`job_step.cost_usd`, `llm_call`) and the code.
Goal set by the owner: routine daily AI spend, with no thesis or research runs, of about **$0.20**.

## What happened on 2026-10-16

| | USD |
|---|---|
| Total AI spend that day | 33.26 |
| Gemini 3.8 Flash, thesis section writing | 29.13 |
| OpenAI (thesis authoring, claim checks, news, escalations) | 4.09 |

The thesis batch ran on Gemini, not OpenAI. The active `research_profiles` config (version 29,
2026-10-06, kept in version 94) sets the `deep-thesis` profile to `gemini-3.8-flash` at `high`
effort. OpenAI (`gpt-6.1-sol`) only writes the final thesis from the finished sections
(`author_thesis`, $1.91 for 33 calls). That is why the Gemini prepaid credit ran out first.

Where one thesis's money goes: 9 sections per thesis, each about 25k input and **15k output**
tokens (mostly thinking at `high` effort), and about 2 Google searches. That comes to $0.80 per
thesis that succeeded.

| Gemini cost part (10-16) | Amount | USD | Share |
|---|---|---|---|
| Output tokens (incl. thinking) | 4.2M × $3.75/M | 15.75 | 57% |
| Google searches | 523 × $14/1000 | 7.32 | 26% |
| Uncached input | 5.9M × $0.75/M | 4.40 | 16% |
| Cached input | 2.0M × $0.075/M | 0.15 | 1% |

Lost to failed runs: $4.83 of $29.71 (16%).

| Cause | Jobs | USD |
|---|---|---|
| OpenAI HTTP 520 at the authoring step threw away finished Gemini sections | 2 | 1.95 |
| Config saved while runs were going ("configuration changed"), during the audit | 8 | 1.66 |
| Thesis refused by validation (scenarios not bear < base < bull) | 2 | 0.96 |
| Worker restarts past the queue's read limit | 2 | 0.26 |

## Routine spend today (last 7 days, no thesis or research): $0.96/day

| Job | $/day | Of which web search | Driver |
|---|---|---|---|
| news_ingest | 0.357 | 0.30 | story corroboration: up to 3 rounds × 10 searches each |
| news_ai_search | 0.216 | 0.20 | daily, 10 holdings × 3 model web searches |
| event_escalation | 0.153 | 0.06 | impact notes on `gpt-6.1-sol`, up to 15 searches |
| news_triage | 0.105 | 0 | hourly; output tokens ≈ input tokens (reasoning) |
| earnings_check | 0.031 | 0 | event-driven, keep |
| news_brief | 0.029 | 0 | Gemini Flash |
| daily + weekly digest | 0.020 | 0 | Gemini Flash writer |
| everything else | 0.04 | 0 | autopilot, inbox, analysis, exposure, chat |

Web searches are 58% of routine spend, at $10 (OpenAI) to $14 (Google) per 1,000.

## Step 1: config only (no deploy), about $0.96 → $0.30 a day

Each change is a new version of the jobs config (or the research profiles config), saved only when no thesis or research job is running.

| # | Setting | From → to | Expected $/day |
|---|---|---|---|
| 1 | `news.corroboration_rounds` | 3 → 1 | news_ingest 0.36 → 0.15 |
| 2 | `news.corroboration_max_searches` | 10 → 3 | news_ingest → 0.10 |
| 3 | `schedules.news_ai_search.cron` | daily → Mon and Thu `20 6 * * 1,4` | 0.22 → 0.06 |
| 4 | `news.ai_search_max_holdings` / `ai_search_max_searches` | 10 / 3 → 6 / 2 | → 0.03 |
| 5 | `escalation.model` | `gpt-6.1-sol` → `gpt-6-luna`, effort `medium` | 0.15 → 0.06 |
| 6 | `escalation.max_searches` | 15 → 4 | → 0.03 |
| 7 | `news.triage_effort` | default → `low` | 0.105 → ~0.05 |
| 8 | news brief and digest writer model | `gemini-3.8-flash` → `gpt-6-luna` | 0.05 → 0.01 |

Projected total: about **$0.30 a day**.

Risks:
- Fewer corroboration rounds means a story stays "unconfirmed" longer.
- Luna impact notes are plainer. Read a week of them before keeping 5.

## Step 2: free search API (key plus one code change), $0.30 → about $0.20 a day

- Add `BRAVE_API_KEY` in Railway (it was already on the missing-keys list).
- Switch `news.ai_search_provider` to `brave` after the lead-recall check the setting asks for. That job's search fee then goes to about zero.
- **Code change:** give story corroboration and escalation the same `search_via_api` path the chat
  already has (`ChatSettings.search_via_api`, Brave results plus a few-day cache). Today only chat
  and news_ai_search can use it. The search fee drops from ~$0.10/day to about zero, and the model
  only reads the titles and snippets.
- Check Brave's current price or free quota before relying on it. Even at a few dollars per 1,000
  queries it costs less than the $10–14 per 1,000 the model providers charge.

Projected total: **about $0.20 a day**.

## Step 3: theses and research (Gemini), $0.80 → about $0.35 per thesis

The thesis batch was a one-off. With every thesis fresh, the autopilot only queues research when a
review date passes (`research_min_days` 30) or an event fires. That was about 7 runs a week this
week, mostly audit runs. Make each run cheaper:

| # | Change (research_profiles, `deep-thesis`) | Effect |
|---|---|---|
| 1 | `effort` high → medium | thinking output roughly halves; the output share is 57% of the cost |
| 2 | `write_order` → `summary_last` | executive summary, KPIs and open questions read the written sentences, not the whole material |
| 3 | `cheap_sections` = strategy_fit, open_questions, executive_summary | written by luna; a weak draft is rewritten by Flash automatically |
| 4 | `search_mode` → `scouts` (luna, low effort) | searches at $10 instead of $14 per 1,000, and the scouts' extracts are shorter than raw pages |

The code says these levers should change only after the benchmark comparison passes. Run it on
the three holdings still without a new thesis (ASML, D4S, NVDA): one run with today's profile and
one with the levers, compared on the benchmark's checks.

## Step 4: stop paying twice for failed runs (code)

1. **Resume authoring, not the whole run.** A failed `author_thesis` call (HTTP 5xx) or a thesis
   refused by validation should retry the authoring step from the saved sections (they are in
   `job_checkpoint`). Today it fails the job, and the redo writes all 9 sections again.
2. **Retry the authoring call on 5xx** with backoff before failing (the 520s were Cloudflare-side and passed).
3. **Don't fail a running thesis when an unrelated config domain changes.** A thesis run should
   compare only the config domains it reads, not every save.

## Step 5: guardrails

- The budget config has `monthly_cap_usd` 75 (raised during the audit) and `per_job_cap_usd`
  research 3. Set `per_job_monthly_cap_usd` for `thesis` and `research` (for example $10 each) so
  a batch can't empty a prepaid credit in one evening.
- Add a per-provider daily alert. The budget tracks spend by model, so an alert when one provider
  passes $5 in a day would have fired around 18:00 instead of the morning email from Google.
- Keep both prepaid balances above one batch's worth. A full rerun of 31 theses costs about $25
  today and about $11 after step 3.
