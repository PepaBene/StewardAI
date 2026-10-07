# Public-disclosure migration map

Plan P0.5, written **2026-10-07** from the migrations in `supabase/migrations/` (not from a live database). **Not yet measured:** current row counts and the false-positive rate of existing suggestions; both need read access to the deployed database and are listed under "Baseline still to take".

## Principle

The shared evidence tables (migration `20261007100000_disclosure_foundation.sql`) reference existing rows and never become a second copy of them. An existing machine suggestion stays unverified until a reviewer decides it; backfills never promote.

## What is extended, what is shared evidence

| Existing table(s) | Role today | In this plan | Rule |
|---|---|---|---|
| `source_document`, `source_document_version`, `source_evidence`, `source_record_evidence` | Versioned documents, quotes, links from domain rows (`sources/documents.py`) | **Extended** in place: issuer, form, fiscal period, language, source key, first-seen, byte length, archive path, date notes, quarantine, parser version; span detail on evidence | Additive columns only. Version and evidence rows stay append-only. Old rows keep null new columns; no backfill rewrites them. |
| `source_observation`, `source_conflict`, `coverage_status` | Governed macro/market observations, conflicts, provider coverage | Unchanged. `disclosure_observation.source_observation_id` can point at a row here | New disclosure facts go to `disclosure_observation`; `coverage_status` keeps provider coverage, `disclosure_coverage` holds the per-entity disclosure state machine |
| `issuer`, `security`, `security_listing`, `issuer_alias`, `issuer_relationship`, `identity_match_decision`, `instrument_security_link` | Canonical identity, dated aliases and succession, reviewed instrument links | **Extended**: `security.share_class`; new `disclosure_entity_mapping` for document mentions | Disclosures join a portfolio instrument only through a *confirmed* `instrument_security_link`. Succession is read from `issuer_relationship` with `known_at`. |
| `provider_access`, `provider_endpoint_manifest`, `provider_fetch_audit`, `provider_quota_usage` | Free-tier gate, budgets, fetch audit | **Reused** by the new fetch governor; each disclosure source gets a `provider_access` row | `disclosure_source.enabled` additionally requires a recorded access test |
| `form13f_*`, `institutional_holding` | US 13F positions | Unchanged until P3 | 13F stays a position layer; never merged with 13D/G |
| `control_stake_*`, `schedule13` | 13D/G stakes | Unchanged until P3 | Separate channel |
| `insider_filing`, `insider_transaction`, `insider_filing_ticker`, `pdmr_*` | Forms 3/4/5 and UK PDMR | Unchanged until P4 (enrichment table lands then) | Original codes and values stay |
| `filing_hit`, `relationship_disclosure`, `company_graph_edge`, `entity_relationship`, `relationship_contradiction`, `graph_*` | Keyword-derived SEC hits, relationship disclosures with status, the graph | **Referenced** by `extracted_claim.legacy_table / legacy_key` in P5 | `suggested` stays `machine_suggested`; none is auto-promoted. `filing_hit` rows are unverified keyword matches and are audited, not trusted (P5.1). |
| `financial_statement_fact`, `xbrl_filing`, `segment_revenue`, `shares_outstanding`, `fundamentals` | Selected XBRL facts and segment revenue | Unchanged until P2; P2 links chosen facts to the filing known at the as-of date | |
| `forward_observation`, `issuer_ir_release`, `issuer_ir_source`, `company_event` | Forward estimates, IR releases, events | Unchanged until P6/P7 | Management guidance is never promoted to consensus |
| `web_fact`, `fact`, `evidence` (blob store) | AI-extracted web facts and archived pages | Unchanged. The archive (`steward.storage`) is reused for original disclosure bytes | Same SHA-256 keyed bucket |

## New in P1 (all owner-read RLS, backend write)

`disclosure_source`, `disclosure_observation`, `extracted_claim`, `disclosure_coverage`, `quality_review`, `quality_review_event`, `disclosure_entity_mapping`. Phase-specific tables (ownership disclosure, insider enrichment, relationship claim detail, deals, estimates) land with their own phases.

## Extended in P3 (`20261007110000_ownership_known_at.sql`)

`form13f_filing`, `control_stake_filing` and `nport_filing` gain `known_at` (end of the filing day, UTC, filled by trigger when not given; never earlier than the filing). `form13f_filing` also gains `confidential_treatment` (`unknown` until a source says otherwise). No existing column, key or row is rewritten; the rollback drops only what was added. P2 reads `financial_statement_fact` and the ownership tables where they are; it adds no table.

## Extended in P4 and P5 (`20261007120000_insider_and_dependency_shapes.sql`)

`insider_filing` gains `known_at` (same rule as above). One new table, `insider_classification_override`: append-only, a reason of at least 10 characters, the original class kept beside the new one. `relationship_disclosure` gains `claim_shape`, `counterparty_count`, `denominator_kind` and `period_end`, backfilled only from the existing anonymous flag; denominator and period stay null until set, so old claims are left out of concentration rather than guessed. The two new constraints are `NOT VALID`, so old rows are not rewritten. The rollback drops the override ledger (data loss, stated in its header) and the added columns.

## API consumers to keep working

Existing owner routes read the extended tables with explicit column lists (for example `api/graph_routes.py`, `api/control_stake_routes.py`, `api/insider_routes.py`); added nullable columns do not change them. The new surface is the `/disclosures/*` router only.

## Baseline still to take (needs the deployed database)

1. Row counts per table above and per regulator in `source_document`.
2. The false-positive rate of current `filing_hit` / `relationship_disclosure` suggestions on a reviewed sample (P5.1 defines the labels; this is the pre-extension baseline).
3. Share of held/watchlist instruments with a confirmed `instrument_security_link` (the gate for joining disclosures to the portfolio).
