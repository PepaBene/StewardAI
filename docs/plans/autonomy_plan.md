# Autonomy plan: the owner only looks and trades

Written 2026-10-13. Source: a sweep of the backend routers, `config/jobs.py`, `approvals/ai.py`,
`quality/cockpit.py` and the web pages under `apps/web/src/app/(app)`. The sweep read routers and
config, not every handler body, so each `file:line` below is a starting point to verify before
implementing. Nothing in this plan is implemented yet.

## Status (2026-11-10): phases C, D and E implemented in part

Built:

- **Ops watchdog** (`steward/ops/`, job `watchdog`, hourly at :10, migration
  `20261110100000_ops_task.sql`): retries failed collectors, queues the refresh job behind a
  data-quality issue (stale price, unresolved identity, fund weights, source conflict), lifts
  provider blocks after `block_reset_days`, and rebuilds benchmark index weights and the fund
  coverage ledger. A problem is retried every `retry_hours` up to `max_attempts`, then shown
  in Needs-you as a notice. Also missing-key notices (14-key catalogue, dismissible) and a
  budget forecast notice (D1, D2, D3, D5, D7, C2).
- **Thesis upkeep** (C5): drafts for the heaviest held stocks without a thesis, an announcement
  notice when the draft lands, and review dates pushed out only when every KPI passes and the
  latest research proposes no change. Each move is a `theses` config version.
- **Lessons** (E1): wrong decisions are shown to the judge as data, and two confident mistakes
  in 90 days raise that kind's confidence floor (`autopilot_learned`, migration
  `20261110110000_autopilot_learned.sql`). The floor only ever raises the configured value.
- **Rebalance suggestion** (E3, in part): when positions leave their bands, a daily notice
  with the planner's trades. Nothing is sent to a broker.

Not built, on purpose or for lack of input:

- C1 fund KID/factsheet extractor; C3 company-input fillers beyond the existing jobs.
- C4 auto-journal/switch log: the owner's record stays the owner's.
- D6 release-gate automation; provider failover chains.
- E2 self-tuning config: there is no engagement signal to tune against.
- E3 thesis-break and opportunity suggestions.
- D5 is a forecast notice only (it does not throttle spend). D4 needed no code: the scheduler is
  complete and `weekly_digest` succeeds. C6 and C7 were already covered by existing jobs.

## Earlier status (2026-11-09): phases A and B implemented

Built: the ledger (`auto_decision`, migration `20261109100000_autopilot.sql`), the judge loop and
calibration (`steward/autopilot/`), the `autopilot` (every 6 hours) and `autopilot_calibration`
(weekly) jobs, the `/autopilot` API and page, and 20 judged queues in `autopilot/kinds/`:
fact, web_fact, graph_edge, graph_event, graph_disclosure, relationship, disclosure_review,
adjudication, insider_override, identifier, deal_party, estimate_pair, listing, peer, news_story,
escalation, inbox_action, agent_note. Plus the stale-thesis research trigger (B8).

Deviations from the plan, and why:

- **Every kind starts in shadow** (decides and logs, applies nothing) and is promoted by
  calibration only. A kind with no `revert` (identifier, deal_party, estimate_pair) is never
  promoted automatically; set it to `auto` in Jobs config → autopilot_settings if you want it.
  The revision-alert gate does not count the autopilot's `estimate_pair` labels.
- **Listings and peers stay on `approvals/ai.py`**; the new `listing`/`peer` kinds are only the
  second stage (stronger model, expiry). Not re-implemented on the framework.
- **Thesis proposals** are a 21st kind, `thesis_proposal`, with three fences: it edits only an
  existing thesis, only `exit_conditions` and `pre_mortem` (never `why_owned`, KPIs or
  scenarios), and calibration never promotes it (`promotable=False`; set `auto` in Jobs config
  yourself). Each accept is a `theses` config version; revert rolls back to the one before it.
- **Per-claim review is not automated, on purpose**: claims already carry the verifier's
  `machine_checked`/`flagged` state, nothing downstream waits on the owner's mark, and a new
  "autopilot reviewed" state would only duplicate the verifier.
- **Identity override** has no stored candidate mappings to choose from, so no judge is
  registered (`approvals.identity` is defined but unregistered). Needs a step that writes
  candidates from OpenFIGI/ESMA first.
- **"Needs you" is ordered by portfolio weight** (latest snapshot), age as the tiebreak.
- **Fixed**: `write_listing` keeps any decided listing on re-derive. The revision-alert gate
  ignores `estimate_pair` labels by the autopilot, so only the owner's labels count.
- `insider_override` has no queue table, so its open set stays a heuristic (code J / unrecognised
  lines with footnotes).
- DB-backed autopilot tests run against `STEWARD_TEST_DATABASE_URL`.

## Goal and principle

The owner's only recurring jobs are: look at the app, decide, trade. Everything else (reviews,
data filling, conflict resolution, job triggering, config upkeep, key/provider health) is done by
the app. The owner is pulled in only for things that are genuinely theirs:

- **Trade decisions** and the investment philosophy (strategy targets, risk appetite).
- **Secrets** that only they can obtain (an API key signup) and **spend ceilings**.
- **High-stakes, low-confidence** items the app cannot settle, batched into one short digest.

Design rule: *auto-decide, log, make reversible, sample-audit.* Never silently. Every automatic
decision records who/what/confidence/evidence, shows in one "Autopilot log", and can be undone in
one click. Trust is earned by measured precision (section A3), not assumed.

## What exists today (do not rebuild)

- Cron in `config/jobs.py` (about line 2038-2391, hot reloaded): sync, prices, news, earnings,
  ownership, disclosure (7 jobs), valuation, XBRL, IR discovery, gap research, graph review,
  `ai_approvals` (daily 07:10), `inbox_scan`, retention and audits.
- Event driven: `earnings_check`, `event_escalation`, `inbox_agent`, `thesis_verdicts`.
- AI auto-approve exists for **only three things**: listings, peers (`approvals/ai.py`, rules
  first, then an LLM at `min_confidence` 0.8) and web facts from `gap_research`. Below the
  threshold items stay `suggested` forever.
- Other auto behaviour: news stories auto-resolve after 14 days, fact reconcile auto-quarantines
  mismatches, quality snoozes expire, budget guard downgrades/pauses at the monthly cap.
- Default off: `shadow_snapshot`, `shadow_report`, `sec_fsds_backfill`, `pdmr_uk`,
  `batch_extraction`, `browser_fallback`.

## Inventory: what still needs the owner, and the automation answer

Burden: **R** recurring, **S** one-time setup, **O** optional/on-demand.

### 1. Review queues that only grow (biggest burden, all R)

| Queue (page / endpoint) | Today | Automation |
| --- | --- | --- |
| Held/quarantined facts, `POST /facts/{id}/review` | nothing releases them | **Fact judge**: re-check against second source (XBRL vs. filing text vs. IR release), rule-compare, LLM tie-break; auto-accept if sources agree within tolerance, auto-correct if one authoritative source wins, auto-reject if unsupported |
| Web facts below 0.8, `/web-facts/{id}/decision`, domain tiers `/web-domains` | held | Second-pass verifier with a different model; corroboration count across independent domains; learn domain tiers from outcomes |
| Relationship suggestions confirm/reject (`reference_routes.py:255`), company-graph edge/event/disclosure decisions (`graph_routes.py:59,72,88`) | graph_review only pre-reviews | Same judge pattern with citation requirement; auto-write the reason text (the 10+ char rule is satisfied by the evidence summary) |
| Disclosure `quality_review`, adjudication slots (0 of 110 read), identifier LEI/FIGI, deal parties, estimate pair reviews, insider override | reason mandatory, never auto-closed | Adjudicator agent with evidence bundle; auto-close on high confidence; sample 5-10% for the audit trail |
| Peer set status, listing approval below threshold | stays `suggested` | Escalating ladder: rules, cheap model, strong model, then expire to "excluded, low confidence" after N days |
| Identity override (type a ticker/ISIN/mapping) | manual | Resolver chain: OpenFIGI, exchange/ISIN suffix rules, SEC/ESMA tables, then LLM web check; owner only sees unresolved holdings that matter by weight |
| Disputed news stories resolve/close, escalations ack/resolve, news feedback, ops incidents | manual | Auto-resolve with a "resolver" summary; escalations auto-acknowledge once the downstream check (thesis verdict, earnings check) completes |
| Inbox confirm/decline of proposed actions; agent notes accept/decline | agent never starts a job itself; items only grow | Policy-gated auto-confirm for safe idempotent kinds (`earnings_check`, `portfolio_sync`, `research` within budget); TTL then auto-decline stale items; notes auto-accepted when sourced and non-conflicting |
| Research: draft plan, approve, start, per-claim review, thesis-proposal accept | fully manual, `research` never scheduled | Scheduled research on trigger (thesis staleness, big news, earnings); auto-approve plans from templates inside the budget; claim review by the verifier (`verify/claims.py`); thesis-proposal accepted automatically only as a *draft version*, never overwriting without a diff the owner can see |
| Quality cockpit issues (read-only; human action is "snooze") | report only | Each issue type gets an owning repair job (see section 3); snooze becomes the exception |

### 2. Manual data entry with no filler

| Data | Today | Automation |
| --- | --- | --- |
| Fund factsheet, facts, NAV, coverage ledger, charges from KID | typed in (`fund_routes.py`, `fund_research_routes.py`) | KID/PRIIPs and factsheet PDF extraction with the existing doc retrieval + extraction pipeline; NAV from price providers; ledger auto-refresh |
| X-Ray regions, index weights, supply edges, macro exposures (derive endpoints are manual triggers) | typed or button | Schedule the derive jobs after every holdings/look-through change; index weights from provider/fund holdings |
| Company inputs: forward data, IR sources/releases, company events, patent families, relationship disclosures, corporate actions, segment map, peer groups, case items | typed | Fill from filings (XBRL segments, 8-K/ESEF/IR), `ir_discovery` and `gap_research`, corporate actions from provider feeds + transactions reconciliation |
| Exchange closures, holiday calendar | `/reference` entry + `holiday-refresh` job | Schedule `holiday-refresh` yearly and monthly verify |
| Watchlist add | manual | Optional "proposed watchlist" from Discover screens and peer graph, auto-added only when it meets the strategy screen; the owner can still add |
| Journal entries, switch log | manual | Auto-journal on every detected transaction (trade, thesis verdict change, escalation); owner's note field optional |
| Report dates (ASML, AZN, TSM, QNT) | owner | Earnings calendar from IR + provider cross-check, scheduled |
| Theses and strategy targets (`PUT /config/theses`, `/config/strategy`) | hand written; `thesis_gap` only reports | **Draft theses for every holding lacking one** (`thesis-draft` exists, on demand), auto-refresh review dates, propose strategy changes from drift; the owner approves philosophy-level changes only (see "stays human") |
| Config upkeep (jobs, news sources, event rules, notifications, prompts, budget, verification, research profiles) | PUT by hand | Config defaults that self-tune within bounds (section 4); no routine editing |

### 3. Manual triggers and health

- Many `POST /jobs/*` buttons ("Run now"). Most are scheduled already. Not scheduled: `research`,
  `earnings_check` (event only), disclosure `mirror-facts`, `extract-notes`, `weekly_digest`
  (never succeeded). **Schedule or event-wire all of them**, then remove the buttons from the
  default view (keep under an "Operator" tab).
- Quality repairs by issue type: `stale_price` (fallback provider chain + backfill),
  `identity_unresolved` (identity chain above), `fund_weights` (look-through refresh),
  `claim_review` (claim verifier), `source_conflict` (fact judge), `provider_access/quota`
  (circuit breaker + provider failover), `thesis_gap` (thesis drafter).
- **Provider/key failures only alert today.** A missing key silently disables a provider job.
  Add provider failover chains, an automatic block reset after a cool-down (`reset-block` is
  manual), and a single "needs you" notice that names the one key to paste and links to the signup.
- Release gate (`release/activation.py`: "nothing does so for you"): auto-switch-off stale
  features, auto-run audits, auto-open defects from failing checks; the pilot/shadow runs
  (`shadow_snapshot`, `shadow_report`) get enabled and judged automatically.
- Budget guard pauses at the monthly cap and the owner raises it by hand. Add an *automatic
  triage*: when the cap nears, drop the lowest value-per-dollar jobs first (measured by
  route stats), and ask the owner a single yes/no only if high-value work would be cut.

## Plan, in phases

Each task lists what to change, where, and how to verify. Phases are ordered by owner time
saved per effort. Sequencing note: M* (Morningstar) and Q* (quant) plans stay as they are; the
`A*` tasks below are additive and reuse their jobs.

### Phase A. Autonomy foundation (do first, everything else depends on it)

- **A1. Autopilot decision ledger.** One table (`auto_decision`): `kind`, `subject_id`,
  `decision`, `confidence`, `evidence` (JSON, citations), `model/rule`, `decided_at`,
  `status` (`applied|reverted|sampled_ok|sampled_wrong`), `reversible_payload`. Every auto path
  writes here. Verify: a migration under `supabase/`, an API list/revert endpoint, a unit test that
  an applied decision can be reverted to the prior state.
- **A2. Generic judge framework.** Extract the pattern of `approvals/ai.py` (rules, then LLM
  verdict with `Field(ge=0, le=1)` confidence, apply at `min_confidence`) into a shared
  `autopilot/judge.py` taking: item loader, rule checks, prompt, schema, apply and revert
  callbacks, per-kind policy. Re-implement listings, peers and web facts on it as the first
  proof, with no behaviour change (existing tests must pass unchanged).
- **A3. Policy config + calibration.** New config domain `autopilot` (hot reloaded like `jobs`):
  per kind `mode` = `off | shadow | auto`, `min_confidence`, `escalate_model`, `sample_rate`, `ttl_days`,
  `daily_cap`. Default every new kind to **shadow** (decides and logs, applies nothing).
  A weekly `autopilot_calibration` job compares shadow decisions to later owner overrides and to
  sampled audits, computes precision per kind, and **promotes shadow to auto when precision is at
  or above 0.95 over 50 or more decisions**, demotes automatically below 0.90. This is how trust is earned.
- **A4. The owner digest ("Needs you").** Replace the inbox/quality/escalation queues as the
  default landing with one prioritized list: only items still `human_required` after the ladder,
  ranked by portfolio weight, with the app's recommendation and a one-tap accept. Target: median
  under five items per week. Includes the weekly "autopilot report" (what it decided, accuracy,
  cost).
- **A5. Reversal and audit UI.** `/autopilot` page: filter by kind, see evidence, one-click revert,
  mark wrong (feeds A3). Uses the existing Industry design system (load the frontend-design skill
  when building).

### Phase B. Clear the review queues (largest owner-time win)

- **B1. Fact judge** for held/quarantined facts and `source_conflict` issues. Reuse
  `earnings/reconcile.py` plus `verify/` (claims, eval). Verify with the golden earnings fixtures
  in `tests/golden/earnings` and a replay of historic owner decisions as the test set.
- **B2. Web-fact second pass and domain tier learning.**
- **B3. Graph and relationship judge** (edges, events, disclosures, relationship suggestions) with
  mandatory citation; auto-writes the reason text.
- **B4. Disclosure adjudicator**: `quality_review`, adjudication slots, identifier suggestions,
  deal parties, estimate pairs, insider override. Use the existing `adjudication prelabel` as
  stage one.
- **B5. Escalation ladder** for listings/peers/identity: rules, small model, strong model, expiry.
  Add `ttl_days` expiry so no queue grows without bound.
- **B6. News and escalation resolver** (disputed stories, escalations, feedback, incidents).
- **B7. Inbox and agent-note policy**: allow-listed action kinds auto-confirm within budget; TTL
  auto-decline; extend allowed kinds beyond `earnings_check/research/portfolio_sync` only after
  calibration (A3).
- **B8. Research autopilot**: schedule/trigger `research` (stale thesis, large news, earnings),
  auto-approve template plans, claim verifier replaces per-claim review, thesis proposals land as
  a *draft version with a diff*, accepted automatically only for non-philosophy fields (facts,
  catalysts, review dates), never for stance/position-size fields.

### Phase C. Fill the data automatically

- **C1. Fund data extractor**: KID/factsheet PDFs into factsheet, charges and facts via the
  existing documents + retrieval pipeline; NAV job; coverage ledger auto-refresh.
- **C2. Scheduled derives** for X-Ray inputs, supply edges, index weights, macro exposures,
  triggered by holdings or look-through changes.
- **C3. Company-input fillers**: segment map and forward data from XBRL/filings, IR sources via
  `ir_discovery`, corporate actions via provider feed and transaction reconciliation, case items
  from the thesis, patent families via EPO.
- **C4. Auto-journal and event-driven switch log** from detected transactions and verdict changes.
- **C5. Thesis drafter**: for any holding lacking a thesis, draft one (existing `thesis-draft`),
  store as a *proposal*, notify once. Auto-refresh review dates when the verdict job confirms
  the thesis. Owner approves only new stance changes.
- **C6. Calendar and closures**: schedule `holiday-refresh`, auto-fill exchange closures, fill
  report dates from IR sites with provider cross-check.
- **C7. Fix remaining open defects the data-completeness work flagged** (see
  `docs/plans/empty_data_fill_plan.md`: price symbol mismatch, reconstructed portfolio history,
  TTM revenue). Autonomy does not work on data the app cannot read.

### Phase D. Health, keys, cost

- **D1. Provider failover and auto block-reset**, with per-provider circuit breakers.
- **D2. Key concierge**: one "needs you" entry per missing key (EDINET, DART, Companies House,
  etc.) that names exactly what it unlocks, ranked by the data it would add. Only the paste is human.
- **D3. Repair job per quality issue type** (table in section 3), so the cockpit shows
  history of fixed issues and open issues only when repair failed twice.
- **D4. Scheduler completeness**: schedule/event-wire the unscheduled jobs; make `weekly_digest`
  succeed (find why it has never run); enable `shadow_*`, `batch_extraction` and
  `browser_fallback` after a one-week shadow run.
- **D5. Self-tuning budget triage** using `llm/route_stats`; ask the owner only when high-value
  work would be dropped.
- **D6. Release gate automation**: auto audits, auto-disable of stale features, auto-defect on
  failing checks.
- **D7. Watchdog**: a daily "app health" job (jobs that missed schedule, queues over TTL, error
  rates, cost vs. cap) that fixes by retry first and notifies by Telegram/Resend only if unfixed.

### Phase E. Self-improvement loop

- **E1. Learn from the few overrides.** Every owner override or "mark wrong" becomes a labeled
  example; recalibrate thresholds and add few-shot examples to prompts (reuse `verify/eval.py`
  and `agent/regress.py`, `replay.py`).
- **E2. Self-tuning config**: news source weights, event rule thresholds, notification volume,
  from engagement (what the owner opened/clicked) within bounded ranges; every change versioned
  through the existing config history so it is revertable.
- **E3. Proactive suggestions**: drift, concentration, thesis-break and opportunity alerts
  written as a ready-to-trade recommendation ("what to do, size, why, evidence"), since this
  is the one output the owner acts on.

## What stays human (by design)

1. Placing trades (read-only broker sync; do not add order placement without an explicit decision).
2. Strategy, targets, risk appetite, and changes to thesis stance.
3. Pasting API keys / spending beyond the monthly cap.
4. Reversal of anything the autopilot got wrong, via one click.

## Risks and guardrails

- **Silent wrong decisions**: shadow-first mode, calibration gate, sampled audit, revert (A1-A3).
- **Runaway LLM cost**: per-kind `daily_cap`, existing `budget_guard`, cheap-model-first ladder.
- **Feedback loops**: AI judging AI output; use a different model/provider (see
  `docs/plans/multi_provider_model_routing_plan.md`) and require evidence from a non-LLM source
  where one exists (XBRL, filings).
- **Config churn**: any self-written config goes through config history and is revertable.
- **Empty queue is not success**: auto-closing must not hide real problems; digest shows counts
  and accuracy so a spike in auto-rejects is visible.

## Success metrics

- Owner-required items per week: under 5 (baseline: measure now, the queues are only growing).
- Share of review decisions made automatically: over 90% after calibration.
- Auto-decision precision per kind: at or above 0.95 on sampled audits.
- Queue age: nothing older than `ttl_days`.
- Jobs missing schedule: 0; keys missing: surfaced within one day.
- Monthly LLM spend within budget, cost per auto-decision tracked.

## Suggested order

A1, A2, A3 (foundation), then B1 and B5 (largest backlogs), D4 and D1 (cheap health wins), then
B3/B4, C5, B8, C1 to C3, A4/A5 in parallel with B, then D2, D5 to D7, and E last once there is
override data to learn from.
