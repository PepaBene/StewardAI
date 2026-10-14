# Autonomous engine plan: from autopilot to a self-improving, self-verifying engine

Status: proposed. Builds on `docs/plans/autonomy_plan.md` (autopilot, ops watchdog, tuning, all shipped).
Companion: `docs/plans/finetuning_data_plan.md` (the data the engine's models are later trained on).

## 0. Fixed rule

**The owner alone reads the app and buys, sells and executes every trade. The engine never trades,
never places an order, never moves money, and never contacts a broker for anything but read-only sync.**
Everything below is about the engine doing the *reading-side* work (collecting, filling, checking,
reviewing, drafting, prioritising, warning) better, with no one in the loop, while the owner can
overrule any of it and the engine learns from that.

## 1. What exists today (short)

- **Autopilot** (`autopilot/*`): 9 judge kinds (approvals, facts, graph, inbox_action, disclosure_review,
  disclosure_data, news_story, thesis). Loop: rules → cheap model → stronger second opinion → apply in
  `auto`, log in `shadow`, else escalate. Ledger `auto_decision`; owner revert/accept/spot-check;
  `calibration.py` moves a kind shadow↔auto; `autopilot_learned` confidence floors; `lessons.py` feeds
  wrong cases into the prompt.
- **Ops watchdog** (`ops/*`): hourly retry-first, then hand to owner as a notice (`ops_task`); tuners for
  alerts, inbox, notices; thesis upkeep; journal gaps; release-gate switch-off.
- **Verification layers**: claim verifier (`verify/claims.py`), source trust, replay of draft configs,
  eval gates in CI (news, research, earnings golden, retrieval, chat), phase 8 `verify_sources`
  (enforce/shadow/signal_audit), quality cockpit (8 checks).
- **Config**: every self-written change is a versioned, revertable config version.
- **Provenance**: engine-created owner-facing records carry `origin: auto` and are excluded from the
  owner's own grading.

## 2. Findings from the research that shape the design

1. **An LLM judge is an advisor, not an authority.** Self-improvement loops that let a model grade and
   promote its own output drift and reward-hack. Every self-change must pass a *deterministic* gate
   (tests, bounds, replay on held-out data) that the model cannot edit.
2. **Calibrated abstention.** Act only when confidence clears a threshold that was fit on
   *human-labelled, held-out* data, per decision kind; otherwise escalate. Thresholds are re-fit, never
   hand-lowered by the engine itself beyond a bounded step.
3. **Champion–challenger.** A new prompt, model, threshold or rule runs in shadow beside the current one,
   is compared on the same inputs, and is promoted only if gates *fixed before the test* pass. Rollback
   is one click.
4. **Reward-hacking audits.** Track proxies the engine could game (escalation rate falling because it
   escalates less, not because it is right; "agreement" with its own second model). Always measure
   against the owner's labels and against outcomes, not against the engine's own opinion.
5. **Overrides are the highest-value signal.** Every owner override must be captured with the reason when
   given, replayed against the decision, and turned into (a) a lesson, (b) a regression case, (c) a
   confidence adjustment. Silent engagement (opened / ignored) is a weaker, separate signal.
6. **Independence beats redundancy.** Two calls to the same model are not two checks. Verification
   should be code, a second source, or a different model family.
7. **Proactivity needs a budget.** The engine may start work on its own only inside daily/weekly budgets,
   with a visible reason, and with a cheap stop.

## 3. Architecture: five layers

```
 Triggers (proactive)  →  Act (existing jobs + judges)  →  Verify (independent)
        ↑                                                         ↓
 Learn (overrides, outcomes)  ←  Review surface (Needs-you, ledger, undo)
                     ↑
        Self-change control (shadow → gate → promote → rollback)
```

### L1. Decision ledger for *everything* (generalise `auto_decision`)
Today only 9 kinds are judged. Every autonomous action in any area (a filled fact, a tuned config, a
drafted note, a retry, a switched-off feature, a source replaced) gets one ledger row:
`kind, subject, inputs hash, decision, confidence, rationale, verifier results, actor (rule / model id /
prompt version / config version), reversible-by, status (applied / shadow / escalated / overridden /
confirmed), outcome links`. One owner-facing "what the engine did" view, filterable by area, with undo.
Done when: no autonomous write exists that is not in the ledger.

### L2. Verification layer (the new core)
For each decision kind define, in code, three independent checks, run before apply:
1. **Invariants** (pure code): ranges, units, sums, dates, currency, dedup, schema, "never overwrite an
   owner-decided row", "never create a row the owner would read as theirs".
2. **Second source** (when one exists): price vs second provider, fact vs XBRL vs press release, identity
   vs two registries, fund weights vs N-PORT.
3. **Different-model audit** (only for judgement kinds): a different family from the actor, sampled at a
   rate that rises when the kind is new or recently overridden.
A decision applies only if all pass; any disagreement escalates. Results are stored on the ledger row.
Plus **a standing re-verification sweep**: every week a random sample of past *applied* decisions is
re-checked against newer data (a fact filled in March vs the filing that arrived in May). Disagreements
open a notice and count against the kind's calibration.

### L3. Override capture and learning
- **Capture**: every place the owner can change something the engine produced gets a uniform
  "Overruled" path (revert / edit / reject) with an optional one-tap reason chip
  (wrong data, wrong source, right but unimportant, too early, too late, prefer my judgement).
  Edits store a diff, not only a flag.
- **Immediate effect**: the overridden item is pinned (`owner_decided`) so the engine never re-does it;
  the lesson is attached to the kind's prompt (already exists for the autopilot; extend to all kinds).
- **Delayed effect**: overrides accumulate per (kind, source, ticker class, rule). When a pattern reaches
  a threshold, the engine proposes a *rule or config change* (not a model change) as a shadow
  challenger; the owner sees "you overruled X 6 times, proposing Y" with a replay of what would have
  changed. Promotion follows L5.
- **Model-level effect**: overrides become labelled training pairs (see the fine-tuning plan).
- **Anti-overfitting**: one override never changes policy; a pattern needs N overrides across M days,
  and a change is bounded to one step.

### L4. Proactive triggers
The engine currently reacts to schedules and alerts. Add a trigger table (`engine_trigger`) with a daily
budget and a visible reason on every run:
- **Staleness**: a holding with no thesis review, no fresh fundamentals, a price source disagreeing.
- **Event-driven**: filing, earnings date, guidance change, rating change, insider cluster, large move,
  macro print → run the relevant refresh + verify + draft chain without waiting for the schedule.
- **Coverage gaps**: the cockpit's "uncovered" list becomes work, not a list.
- **Anticipation**: before an earnings date, pre-build the expected-vs-actual frame; before month end,
  pre-build the review draft; before a rebalance drift crosses a band, warn.
- **Learning triggers**: when a kind's override rate rises, auto-shadow it; when it falls for a month,
  propose promotion.
Rules: every proactive run states why it started; has a per-day cost cap from the budget guard; stops
itself when the owner dismisses the same trigger repeatedly (existing notice tuner generalised).

### L5. Self-change control
Anything the engine changes about itself (prompt version, threshold, rule, source weight, schedule,
model choice) follows one pipeline:
1. **Propose** as a challenger with a hypothesis and the exact metric it should move.
2. **Shadow** on live inputs, no effect on the owner-facing result.
3. **Gate** with criteria written *before* the test: min sample, no regression on the golden set, no
   rise in owner overrides, no rise in cost beyond cap, no rise in escalations for the wrong reason.
4. **Promote** as a versioned config (revertable) with a notice; **auto-rollback** if the next window's
   override rate or verifier-failure rate regresses.
5. **Freeze list**: things the engine may never change on its own - the trading restriction, budget caps,
   the gates themselves, the provenance rule, credentials, anything that touches money.

(L7, chat as a control surface with visual answers, sits beside these layers: see below.)

### L6. Meta-monitoring (watching the watcher)
- Liveness: heartbeat for autopilot, watchdog, each proactive trigger, each tuner; a missed beat is a
  notice delivered by a *second channel* (Telegram if the app is down).
- Delivery verification: confirm Telegram/Resend delivered (provider receipts) and retry/escalate.
- Drift dashboard: per kind - override rate, verifier-failure rate, escalation rate, cost, latency,
  calibration error (predicted vs actual correctness). Alerts when a kind degrades.
- Cost/blast-radius: global kill switch and per-area switches (exist partly) shown in one place.

### L7. Chat as a full control surface (one way among several, never the only way)
Everything the owner can decide or change in the app screens must also be possible from chat, and chat
must be able to answer with visuals. The screens stay; chat is a second door to the same actions.

**Decisions and control from chat**
- **One action layer**: every owner action (accept/revert/correct a fact, confirm/false a story,
  acknowledge an alert, accept/reject a thesis proposal, decide a graph edge or identity, snooze a check,
  dismiss/switch off a notice, change a config, set a mode off/shadow/auto per autopilot kind, pause or
  run a job, roll back a config version, log or edit a journal entry, rate a report) is exposed as a typed
  action in one registry. Screens and chat both call it, so behaviour, validation, audit and undo are
  identical. No chat-only logic.
- **Tool use by the chat model, authority by the server**: the model proposes an action with arguments;
  the server validates, checks permission and the freeze list, then runs it. The model never writes
  directly, and cannot reach actions outside the registry.
- **Confirm before change**: read-only questions run straight away. Any write shows a confirmation card
  (what will change, before/after, what it affects, how to undo) with Confirm / Edit / Cancel. Low-risk,
  reversible actions may be set to "do it and tell me" by the owner per action type. Bulk actions ("accept
  all 14 low-risk facts") show the list first.
- **Every chat decision is an owner decision**: recorded with `via: chat`, the owner's wording and the
  confirmation, so it counts as a human label (strongest signal in L3 and the fine-tuning plan), and it can
  be undone from the ledger like any other.
- **Natural-language overrides and standing instructions**: "stop alerting me about dividend news for
  fund X", "treat this source as low trust", "be more cautious on small caps" are turned into a proposed
  config change or rule (with replay of what it would have changed), shown for confirmation, saved as a
  revertable version. The same path takes "why did you do that?" - chat answers from the ledger row
  (inputs, verifier results, rationale) and offers Revert / Teach ("don't do this again because...").
- **Steering the engine**: "what needs me?", "walk me through today's decisions", "run research on X",
  "re-check this fact", "show what you changed this week and undo the last one" map to ledger, Needs-you,
  jobs and proactive triggers.
- **Trading boundary stays**: chat can never place, prepare for submission, or schedule an order, and can
  not call a broker write API. It can show the drift trade list, explain it, and record that the owner
  traded (journal), but execution happens at the broker by the owner. Requests like "buy 10 X" get an
  explanation and a link to the pre-trade view, not an action. Enforced by the registry having no such
  action, plus a test.
- **Safety**: untrusted text (news, filings, web) can never trigger an action (prompt-injection guard
  applies to chat tool results); rate limit and budget on chat actions; destructive or wide-reaching
  actions (switch a whole area to auto, change budget caps, delete data) need a second explicit
  confirmation; freeze-list items are refused in chat as everywhere.
- **Context**: chat knows the page the owner came from ("this fact", "this story") and the item selected,
  so "approve this" and "why is this red?" work without naming ids.

**Visualised answers**
- Chat replies can contain **structured visual blocks**, rendered by the app's existing chart/table
  components, never free-form model-written HTML/JS: line/area price and performance charts (with
  benchmark, events and thesis markers), allocation bar/treemap/donut, drift-vs-band bars, contribution
  and attribution waterfalls, risk (volatility, drawdown, factor/theme exposure) charts, scenario/stress
  results, fundamentals trend and segment mix charts, estimate vs actual, fund look-through and overlap
  maps, supply/relationship graph, timeline of news and alerts, calibration and override-rate charts for
  the engine itself, and comparison tables with sparklines.
- **Model chooses, code draws**: the model returns a block spec (`chart_type`, data query reference,
  encodings, title, caption); a server-side builder fetches the data from the same read APIs the screens
  use and validates it. The model never supplies numbers (existing chat rule), so every plotted value is
  traceable to a tool result and shows its source and as-of date; tapping a point opens the evidence.
- **Interactive and exportable**: hover values, range switch, toggles for series, "open in full page",
  copy as image/table. A visual that is useful can be pinned to a dashboard or turned into a saved
  screen/report.
- **Accessible and mobile**: every chart has a text summary and a table view; layouts work at phone width.
- **Verification of visuals**: a check compares each plotted series with the source query output, and
  flags empty, stale or partial data in the caption rather than drawing a misleading chart.
- **Engine transparency in chat**: "show me what the engine did this week" renders the ledger as a
  timeline with counts by kind, override rate and an undo list.

## 4. Per-area plan (every area of the app)

Legend: **V** = verification to add, **O** = override path to add, **P** = proactive behaviour, **L** =
what it learns from.

| # | Area | Today | V | O | P | L |
|---|------|-------|---|---|---|---|
| 1 | Portfolio sync, positions, transactions | Broker is truth; drift, breaches, signoff manual | Auto-compare snapshot totals with broker totals and cash; invariant checks (units, FX, duplicates); alert on any difference rather than silently fixing | None needed (broker authoritative); owner can annotate a position | Detect missed sync, partial sync, new instrument, split/ticker change | Which discrepancies were real vs broker lag |
| 2 | Prices, FX, reference, identity | Diagnostics, probes, reconcile, breaker, AI identity | Second source vote per price; stale-price detector with auto fail-over (D1 pattern); identity: two registries agree | Confirm/reject identity; pin a price source for a ticker | Detect delistings, ticker changes, new listings of held names | Rejected identities → rule updates |
| 3 | Fundamentals, XBRL, statements, segments, forward | Validate, reconcile, golden set, facts judge | Cross-statement identities (assets = liabilities + equity, segment sums), restatement detector, regression vs golden set on every extractor change | Facts queue accept/correct/reject (exists); add reason chips | Pre-fetch on filing; chase late filers; fill gaps in the segment map and rubric | Corrections → extractor regression cases |
| 4 | News, events, escalation, alerts, digests | Tiers, claim verifier, 100-item eval, gate, alert tuner | Story-level second-source rule; digest claim check on every claim; track false-alarm and miss rates (missed = owner found it elsewhere) | Confirm/false, ack, feedback marks (exist); add "should have been alerted" | Watch holdings' peers/suppliers; pre-earnings brief | Engagement events (E2) + feedback → source weights, volume (needs weeks of data) |
| 5 | Research (deep, scouts, gaps, IR discovery) | Source policy tiers, injection guard, gates, budget | Every cited claim traced to a stored source; independent fact-check of top claims by a different model; contradiction scan across runs | Plan editor (exists); rate/reject a report with reason | Trigger research from gaps and events (exists); schedule refresh of aging reports | Rejected reports → plan templates; read/not-read |
| 6 | Theses, KPIs, verdicts, reviews | AI-authored thesis, versioned; arithmetic verdicts | Thesis vs evidence consistency check; KPI definitions checked against filings; "thesis stale" invariant | Proposal accept/reject, free edit (exists); capture edit diff | Draft thesis for new positions; re-test on each earnings | Owner edits → author prompt lessons |
| 7 | Company pages | Fact provenance, rubric | Rubric score recomputed from facts independently; page completeness check | Pin/override a rubric score or segment | Fill empty sections from facts | Overrides → rubric tuning proposal |
| 8 | Funds, ETF, look-through, X-ray | fund_weights check, N-PORT, KID facts | Look-through sums to 100% ± tolerance; compare to fund's own factsheet; overlap sanity | Factsheet/ledger editors (exist) | Refresh on new N-PORT/KID; detect fund changes (TER, index) | Corrections → parser cases |
| 9 | Discovery, screens, universe | Owner writes screens; opportunity notices | Backtest the screen with costs and point-in-time data before suggesting; avoid look-ahead | Dismiss/save; tuner switches off ignored kinds (exists) | Suggest screen variants; surface new names matching saved screens | Saved vs dismissed |
| 10 | Decision, rebalance, ranking, risk, quant | Read-only, flags only | **Outcome backtest of past suggestions** (the biggest gap): did the flagged risk or ranked idea play out; second implementation of key numbers (risk, weights) | Owner marks "I did / did not follow, because..." (no execution) | Warn before drift crosses band; propose rebalance trade list (exists) | Followed vs ignored + outcome → which flags matter |
| 11 | Disclosure, insider, ownership, estimates | Adjudication samples, signal_audit | Machine-assisted second labeller on adjudication samples (advisory only) | adjudication, disclosure_review (exist) | Cluster-buy / filing-pattern triggers | Adjudication labels → classifier training data |
| 12 | Graph, relationships, supply edges | Vendors suggest, owner decides | Edge plausibility against two sources; contradiction panel | Decision form (exists) | Re-check edges on 8-K / 10-K changes | Owner decisions → edge-scoring rules |
| 13 | Macro | Source-vintage PIT, AI exposure maps | **Thin today**: add vintage regression checks, exposure-map sanity vs holdings, second-source for each series | Override exposure mapping | Pre-build for scheduled releases | Overridden maps → mapping lessons |
| 14 | Journal, scorecard, switch log | Pure arithmetic, auto-log of trades (origin: auto) | Recompute scorecard independently from raw ledger; reconcile to broker | Edit auto-logged rows; they stay excluded from owner grading unless confirmed | Journal gap notices (exists) | Which auto-logs the owner edited |
| 15 | Chat (control surface, L7) | Rules first, benchmark, model can't supply figures; read-only | Every number must resolve to a tool result; every chart series checked against its source query; log unanswerable questions as backlog | Every owner action available as a confirmable chat action (`via: chat`); thumbs with reason | Offer to run the missing job; proactive "needs you" summary and visuals in chat | Chat decisions are human labels; thumbs + follow-ups → benchmark questions |
| 16 | Inbox, agents | Bounded agent, source-id check, regress/replay | Replay set grows from owner decisions automatically | Confirm/decline (exist) | Auto-open items for engine-detected issues | Decisions → routing stats, severity tuning (exists) |
| 17 | Quality cockpit | 8 checks, snooze | Add checks for every new verifier; every failing check auto-opens an `ops_task` with a fix attempt | Snooze (exists) | Auto-repair before notify | Snoozes that were right → loosen the check |
| 18 | Config, budget, jobs, scheduler | Versioned config, budget guard | Self-changes only through L5; budget forecast and auto-throttle of low-value jobs | Roll back any version (exists) | Reschedule jobs by observed freshness need | Rollbacks → blocklist of change types |
| 19 | Release, readiness, audit | Human source-to-screen ledger; release gate switch-off | **Machine audit may only add mismatches (fail the gate), never add "match" rows** | Owner signs the human audit | Run audit sweep after every release | Missed mismatches → new audit rules |
| 20 | Notifications | Pure routing, code-composed text | Delivery receipts, dead-letter handling, second channel for critical | Mute/snooze per kind | Quiet hours, batching by engagement | Opens (E2) → timing and volume |
| 21 | Autopilot and ops | Ledger, calibration, watchdog | L2 + L6 apply to itself | Revert/accept/spot-check (exist) | L4 | L3 |

## 5. Phased delivery

Each phase ends with tests, a rollback path and a status entry in this file.

**Phase 1 - Ledger, verification and meta-monitoring (foundation)**
1.1 Generalise the ledger (L1): a write-through helper that every autonomous action calls; migrate
    existing ops/tuner/auto-log writes to it. Verify: a test that fails if an autonomous write skips it.
1.2 Verification registry (L2): per-kind `invariants`, `second_source`, `audit` interface; implement for
    prices, facts, identity, fund weights, news stories, digests first.
1.3 Liveness and delivery receipts (L6): heartbeats for every autonomous job, second-channel notice,
    Telegram/Resend receipt check.
1.4 Weekly re-verification sweep of applied decisions.

**Phase 2 - Override capture and learning**
2.1 Uniform "Overruled" path and reason chips across the areas in section 4 (start: facts, news
    story, thesis, graph, identity, journal auto-logs).
2.2 `owner_decided` pinning everywhere; engine never redoes an overruled item.
2.3 Override-pattern miner → rule/config challenger proposals with replay (L3).
2.4 Extend lessons injection from autopilot kinds to research, thesis author, digests, chat.

**Phase 2b - Chat control surface (L7)** (depends on 1.1 ledger and 2.1 override paths)
2b.1 Owner-action registry: typed actions with validation, permission, freeze list, undo; migrate the
     existing screen endpoints to call it. Test: no action in the registry can place or prepare an order.
2b.2 Chat tool layer and confirmation cards; read-only tools first, then reversible writes (facts, stories,
     notices, snooze), then config/mode changes with double confirmation.
2b.3 Page/selection context passed into chat; "why did you do that?" from the ledger with Revert / Teach.
2b.4 Natural-language standing instructions → proposed config/rule change with replay (shares 2.3).
2b.5 Visual blocks: block spec schema, server-side builders over existing read APIs, renderers reusing the
     app's chart components, source/as-of captions, text and table fallbacks, series-vs-source check.
2b.6 Engine views in chat: week-in-review timeline, override and calibration charts, Needs-you digest.
2b.7 Chat benchmark extended with action cases (correct action, correct refusal for trades and injected
     instructions) and visual cases (right chart, right numbers).

**Phase 3 - Self-change control**
3.1 Challenger framework (L5) over prompt versions, thresholds and rules, with gates fixed in config the
    engine cannot write.
3.2 Auto-rollback on regression; freeze list enforced in code and tested.
3.3 Reward-hacking audit job: compares proxy metrics (escalation rate, agreement) to owner-label
    metrics and raises a notice on divergence.
3.4 Calibration dashboard (predicted vs actual correctness per kind).

**Phase 4 - Proactive engine**
4.1 `engine_trigger` table with budgets and visible reasons (L4).
4.2 Staleness, event-driven and coverage-gap triggers; anticipation drafts (pre-earnings, pre-review).
4.3 Learning triggers (auto-shadow on rising override, propose promotion on falling).
4.4 Generalised notice tuner (stops a trigger the owner keeps dismissing).

**Phase 5 - Close the area gaps**
5.1 Outcome backtest of rebalance/ranking/risk suggestions (area 10).
5.2 Macro verification (area 13) and independent scorecard recompute (area 14).
5.3 D6 machine audit that can only fail the gate (area 19); E2 further tuning of news source weights and
    volume once at least 6 weeks of engagement data exist.
5.4 Remaining table rows, one area per PR.

### Delivery status and known gaps

**Phase 1 - delivered in part (2026-10-14).** No migration was needed. What exists: `engine/timeline.py`
and `GET /engine/ledger` (1.1), `JudgeKind.verify` with the fact check and `recheck` (1.2),
`ops/delivery.py` in the watchdog (1.3), `autopilot/reverify.py` in the calibration job (1.4).

Gaps, to be closed before phase 1 counts as done:

1.1 One list of what the engine did
- It is a read model over five tables, not the write-through helper the plan asked for. The test only
  checks that the five sources are listed; it does not fail when a new autonomous writer skips the list.
- Not in the list yet: tuner changes (alerts, inbox, notices), `owner_override` rows, alert deliveries,
  auto-logged journal rows (`origin: auto`), news-story and digest actions that do not go through
  `auto_decision`.
- The `config` source is "versions with no author and a parent"; that also matches versions written by
  jobs or seeds that are not self-changes, so it can over-report.
- `reversible` is only a flag. There is no single undo for config versions and engine changes from the list.
- No web page for the list, no kind filter, no paging, and the OpenAPI schema and web types are not
  regenerated for `/engine/ledger`.

1.2 Verification registry
- Only the `fact` kind has `verify`/`recheck`. Prices, identity, fund weights, news stories, digests,
  `web_fact` and the other autopilot kinds have none.
- The fact check is narrow: the printed number must appear in the stored excerpt. It does not compare the
  value with a second source or the reconciliation, and a `correct_*` decision is not checked.
- There are no `second_source` or `audit` interfaces; only the two hooks exist.
- Nothing fails when a kind has neither a verifier nor a written waiver.
- The quality cockpit has no check for the verifiers (area 17).

1.3 Liveness and delivery receipts
- Heartbeats cover only the autopilot and the watchdog, not every autonomous job.
- The receipt is the `alert` row status. Provider message ids (Telegram `message_id`, Resend id) are not
  stored and Resend delivery webhooks are not read, so "accepted by the API" is the strongest evidence.
- Only the `alert` table is watched; the weekly report and the digest sends are not.
- There is no dead-letter handling beyond the `failed` status.
- The second channel is the in-app notice only. Telegram/email for critical failures is open question 2.

1.4 Weekly re-verification
- It runs on every calibration run, not weekly, and checks the last 30 days of unreviewed applied
  decisions, at most 200 per run.
- Only facts have a kind-specific recheck; every other kind gets only "the item is waiting again".
- The disagreement rate (success metric) is not stored or shown.
- A notice the owner dismissed opens again on the next run while the problem remains.
- It does not sample decisions that were reviewed, and does not use a second source or a different model.

## 6. Success metrics (measured against the owner, not against the engine)

- Owner override rate per kind, trending down; no kind promoted to `auto` with override rate over its cap.
- Verifier-caught-before-apply vs owner-caught-after-apply (the second must trend to zero).
- Weekly re-verification disagreement rate.
- Calibration error per kind (expected calibration error, target under 5 points).
- Time-to-detect for stale/missing data; share of incidents fixed by retry before the owner sees them.
- Owner time in the app spent reading vs fixing (from engagement events).
- Cost per day within budget; proactive runs per day within cap.

## 7. Risks and guardrails

- **Self-reinforcing error**: engine trains or tunes on its own output. Mitigation: only owner-labelled or
  outcome-confirmed data counts as ground truth; `origin: auto` rows are never labels.
- **Reward hacking / metric gaming**: see L5 3.3 and section 6.
- **Over-notification**: proactivity budgets and the generalised notice tuner.
- **Silent drift after a provider change**: golden set run on every provider/model change; shadow first.
- **Prompt injection from news/web into actions**: untrusted-content guard stays on every ingest path; no
  autonomous action takes an instruction from fetched text.
- **Blast radius**: freeze list, per-area switches, global kill switch, every change a revertable version.
- **Never trades**: no code path imports a broker write API; a test greps for it.

## 8. Open questions for the owner

1. Reason chips on overrides: acceptable friction (one tap), or capture silently only?
2. Is a second channel for critical liveness failures (Telegram) wanted at all hours?
3. Daily cost ceiling for proactive runs (suggest starting at 20% of the current daily LLM budget).
4. Which areas first? Suggested order: facts, news, identity, thesis, then the rest.
5. Chat actions: which should run immediately ("do it and tell me") versus always ask? Suggested default:
   ask for everything that writes, relax per action type later.
6. Which visuals do you want first? Suggested: allocation vs targets, performance vs benchmark, drift vs
   band, price with events, engine week-in-review.
