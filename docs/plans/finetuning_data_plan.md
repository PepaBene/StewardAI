# Fine-tuning data collection plan

Status: proposed. Goal: from now on, every model call and every owner judgement is captured so that, later, the
owner can fine-tune (or distil into) models for deep research, news, data analysis, recommendations and
data filling. This plan only collects, curates and measures; it does not train anything.
Companion: `docs/plans/autonomous_engine_plan.md` (overrides and outcomes are the labels).

The owner alone reads the app and trades. Collected data never leads to an engine that trades.

## 1. Principles (from research)

1. **Small, curated, labelled beats large and raw.** A few thousand high-quality examples per task usually
   matter more than millions of noisy ones. Capture everything, train on a filtered subset.
2. **Fix the evaluation set before training.** A golden hold-out per task, never used for training and never
   edited after a model has been scored on it.
3. **Labels come from the owner or from outcomes**, not from the engine's own judge. Engine-produced
   examples without confirmation are "unlabelled" and are used only as candidate inputs.
4. **Preference data is cheap and strong.** Overrides produce natural chosen/rejected pairs (preference
   tuning, e.g. DPO) in addition to supervised pairs.
5. **Stay provider-agnostic.** Hosted fine-tuning availability changes (it was reported to be narrowing;
   verify before relying on it). Store a neutral format and export to any provider or an open-weights
   trainer.
6. **Privacy and licence first.** Personal portfolio data and third-party content are the two risks;
   both are handled at capture time, not at training time.

## 2. What is missing today (from the code survey)

- No per-call LLM log. Only aggregates (`job_step` usage) and a result cache (`ai_result_cache`) exist, so
  full prompts and raw responses are not retained.
- Owner signals exist but are scattered: `auto_decision` reviews (revert/accept/spot-check), `news_feedback`,
  fact review queue, inbox confirm/decline and ratings, escalation acknowledgements, thesis proposal
  accept/reject, graph decisions, identity decisions, adjudication labels, `engagement_event` (opens).
- Eval infrastructure exists (news 100-item set, research gate, earnings golden, retrieval eval, chat
  benchmark) and can seed the golden sets.
- Policy conflict: `news_feedback` carries a comment that it is never used for training. This must be
  explicitly revisited by the owner before any use (open question 1).
- Licensing: full news text and paid-provider data may not be reusable for training. Unresolved.

## 3. Capture layer: `llm_call` (append-only)

One row per model call, written by the central client (`llm/routing.py`, `llm/client.py`) so no call site
can skip it.

| Field | Notes |
|---|---|
| `id`, `created_at` | |
| `task` | enum: `deep_research`, `news_classify`, `news_story`, `digest`, `data_analysis`, `extraction` (facts, KID, segments), `data_fill`, `recommendation`, `thesis_author`, `judge`, `chat`, `identity`, `other` |
| `job_step_id`, `trace_id` | link to the run and parent calls |
| `provider`, `model`, `provider_response_id`, `params` | temperature, max tokens, tools, structured schema |
| `prompt_key`, `prompt_version`, `config_version` | which overridable prompt and config produced it |
| `messages` (jsonb) | full prompt, system + user + tool results, after redaction |
| `response` (jsonb) | raw response, tool calls, structured output |
| `tokens_in`, `tokens_out`, `cost`, `latency_ms`, `status` | |
| `input_refs` | ids of source documents, stories, facts used (not copies of third-party text, see 5) |
| `redaction` | what was removed and with which ruleset version |
| `outcome_id` / links | filled later, see section 4 |

Storage rules: append-only (no update except `outcome` links in a side table), separate schema or bucket
for large bodies, partition by month, owner-only RLS, excluded from app responses. Capture can be switched
off per task (config) and has a size/cost cap; the payload is compressed.

## 4. Labels and outcome linkage

A side table `llm_label(call_id, source, label, detail, created_at, by_owner bool)` fed by hooks on every
existing owner signal. Label strength, strongest first:

1. **Owner correction** (edited fact, corrected story class, rewritten thesis section, changed graph edge):
   gold target = the owner's version; the original is the rejected output.
2. **Owner verdict** (revert, accept, confirm, decline, false alarm, thumbs, "useful"): binary label.
3. **Outcome-confirmed** (a fact later matched the filing; a flagged risk materialised; an alert was acted
   on): label from reality, with the confirming source.
4. **Engagement** (opened, ignored for N days): weak signal, used for ranking tasks only.
5. **Engine-only** (no human or outcome signal): unlabelled; never ground truth. Rows with `origin: auto`
   downstream are never promoted to labels.

Reason chips from the engine plan (section L3) become structured label detail.

## 5. Privacy, redaction and licence

- **Personal financial data**: holdings sizes, account ids, balances, cash, names, emails, broker ids are
  replaced at capture with stable placeholders (`<POSITION_PCT>`, `<ACCT>`), keeping weights as buckets or
  percent when the task needs them. A ruleset version is stored; a nightly scan flags any raw account
  pattern in `messages`. Exports to any external trainer use the redacted copy only.
- **Third-party content**: store references and short quoted spans (fair-use sized) rather than full news
  articles or paid-provider payloads. Per source, record `training_ok` (yes / no / unknown) in a table
  maintained by the owner; unknown is treated as no. Filings and other public-domain regulator documents
  (SEC EDGAR, ESMA) are generally fine; paid vendors, news publishers and X/social content need review.
- **Secrets**: API keys and auth headers are never in `messages` (verified by a scan test).
- **Retention**: raw prompts 18 months, labelled curated sets indefinitely, with a delete-by-subject path.
- **Owner consent switch**: one config flag `capture_training_data` with per-task overrides; default off for
  `chat` until the owner opts in.

## 6. Datasets per task

Each dataset is a materialised view or export job, versioned, with a manifest (count, date range, filters,
label mix, redaction version, golden-set exclusion).

| Task | Input | Target / label | Primary label sources | Notes |
|---|---|---|---|---|
| Deep research | plan, retrieved evidence, instructions | final report, claim list with sources | Report accept/reject, claim verifier results, owner rating, fact-check disagreement | Train for planning, citation faithfulness and refusing unsupported claims; separate retrieval from writing |
| News classification and impact | story text/refs, holdings context | event type, severity, relevance, impact note | Story confirm/false, ack, feedback, "should have alerted", 100-item eval | Highest label density; start here |
| News story clustering and corroboration | candidate items | same-story groups, tier | Owner merge/split, confirm/false | |
| Data analysis and verdicts | structured inputs, filings | analysis text and numbers | Claim verifier (numbers recomputed in code), owner edits | Numbers come from tools, model trained to cite them, not to compute |
| Recommendation (ranking, thesis stance, rebalance narrative, opportunity notices) | candidate set + context | ranked list / text | Accept/dismiss, followed-or-not with outcome, thesis proposal accept/reject | Outcome horizons are long: store the label with an `as_of` and re-score later |
| Data filling and extraction (facts, KID, segments, identity, graph) | document span | structured record | Fact review corrections, auto_decision reviews, golden extraction set | Strongest supervised task; JSON-schema outputs |
| Judges (autopilot kinds) | item + evidence | decision + confidence | Owner revert/accept/spot-check, second-model audits that were later confirmed | Used for a small distilled judge and for calibration |
| Chat | question + tool results | answer | Thumbs, follow-up rephrase | Optional, owner opt-in |

Preference pairs: any overridden or corrected output yields `(prompt, chosen = owner version, rejected =
engine version)`. A second pair type comes from shadow comparisons where the owner-aligned outcome is known.

## 7. Golden and hold-out sets

- Per task, a fixed golden set (target 200-500 items; start from existing eval sets), built from
  owner-confirmed examples, stratified by ticker class, source and difficulty.
- A rolling "recent" test slice (last 30 days) never trained on, to detect drift.
- Contamination guard: examples are hashed; training exports exclude any hash in golden or recent slices
  and near-duplicates (normalised text hash and embedding similarity).
- Golden changes are versioned and logged; a model's score is only comparable within a golden version.

## 8. Export formats

- Neutral canonical JSONL: `{task, messages[], response, label, pair?, metadata}`.
- Adapters: OpenAI-style chat JSONL, Anthropic-style messages, Gemini tuning format, and a plain
  instruction format for open-weights trainers (Hugging Face / Axolotl style). A DPO adapter emits
  `chosen` / `rejected`.
- A dry-run command validates schema, token lengths, label mix and PII scan before any file leaves.

## 9. Quality of the dataset itself

Automated gates before a dataset version is marked usable:
1. Redaction scan clean.
2. Label coverage: at least N owner/outcome labels per task (suggested minimum 500 for SFT, 300 pairs for
   preference tuning, to be confirmed against the first training run).
3. Class balance report; no single ticker or source over 20% of a set.
4. Duplicate/near-duplicate removal.
5. Disagreement review: a sample where the owner label disagrees with outcome is listed for owner check.
6. Licence filter applied (`training_ok`).

## 10. Phased delivery

**Phase 1 - Capture (start now; cheap, irreversible if skipped)**
1.1 `llm_call` table, central client hook, redaction ruleset v1, per-task switch, size cap, RLS, tests that
    secrets and account ids never land in `messages`.
1.2 `source_licence` table and owner screen; default `unknown`.
1.3 Config flag and a "What is collected" page in the app (counts per task, size, last 30 days, off switch).

**Phase 2 - Label linkage**
2.1 `llm_label` table and hooks on: auto_decision review, fact review, news feedback/story confirm,
    inbox decisions, escalation ack, thesis proposals, graph/identity decisions, adjudication.
2.2 Reason chips (engine plan L3) stored as label detail.
2.3 Outcome jobs: re-score recommendations and alerts at 30/90/180 days; fact confirmed-by-filing check.
2.4 Resolve the `news_feedback` "never train" comment with the owner; update code comment and docs.

**Phase 3 - Datasets and golden sets**
3.1 Materialised dataset builders per task (start: news classification, extraction/data filling, judges).
3.2 Golden sets seeded from existing evals; contamination guard; manifest.
3.3 Dataset quality gates (section 9) as a CI-like job producing a report.

**Phase 4 - Export and first baseline**
4.1 Exporters and dry-run validator.
4.2 Baseline: score current prompted models on the golden sets and store the numbers (the bar a fine-tune
    must beat on quality *and* cost).
4.3 First experiment, small and cheap: a distilled extraction or news classifier from owner-confirmed
    data; compare in shadow using the engine plan's champion-challenger gates before any promotion.

**Phase 5 - Ongoing**
Monthly dataset refresh, golden set review, drift report, licence review, retention purge.

## 11. Success metrics

- Share of model calls captured (target 100% of enabled tasks).
- Labelled examples per task and label mix; growth per month.
- Share of labels from the owner or outcomes vs engine-only.
- Golden-set baseline score per task, and later fine-tuned vs baseline on quality, cost per call, latency.
- Zero PII/secret findings in the weekly scan.
- Storage and capture cost within the cap.

## 12. Risks

- **Licence breach** (news/paid data): mitigated by `training_ok` default-unknown, references over copies.
- **PII leak**: redaction at capture, scans, owner-only RLS, exports from redacted copy only.
- **Training on engine errors**: engine-only rows are never labels; golden set comes from owner-confirmed data.
- **Label sparsity**: the owner overrides rarely by design (good engine), so labels accrue slowly; the plan
  therefore adds spot-check sampling (existing) and outcome-based labels, and prefers distilling a strong
  model's verified outputs for tasks with checkable answers (extraction, numbers).
- **Provider terms**: some providers restrict using outputs to train competing models; check each provider's
  terms before using their outputs as targets.
- **Cost of storage**: compression, partitioning, retention.

## 13. Open questions for the owner

1. May `news_feedback` and chat history be used for training (current comment says never)?
2. Which news and data sources have licences that allow training use?
3. Preferred training route: hosted provider fine-tune, or open-weights you host (affects format and PII
   stance)?
4. Retention period for raw prompts (suggested 18 months)?
5. Start capture now for all tasks except chat?
